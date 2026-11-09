import uuid
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase,override_settings
from django.core import mail
from django.utils import timezone
from .models import Part,Location,Stock,Order,Event,Shipment,ReturnReceipt,AlertSubscription,AlertDelivery
from .services import submit_order,transition_order,update_stock,transfer_stock,WorkflowError
from .operations import receive_return,set_hold
from .reporting import stock_summary,due_counts
from .notifications import send_stock_digest
from .tests import DETAILS

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}},AXES_ENABLED=False,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',WMS_EMAIL_ENABLED=True,DEFAULT_FROM_EMAIL='test@example.com')
class ExtendedTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.wh=U.objects.create_user('wh',email='wh@example.com');self.wh.groups.add(Group.objects.create(name='S3PL Warehouse'))
        self.fd=U.objects.create_user('fd',email='fd@example.com');self.fd.groups.add(Group.objects.create(name='FD Corporate'))
        self.part=Part.objects.create(sku='0012',barcode='0099',low_stock_threshold=5,target_stock=20,unit='EA')
        self.stock=Stock.objects.create(part=self.part,location=Location.objects.create(code='P1'),kind='PICK',on_hand=10)
    def order(self,qty=6): return submit_order(self.fd,{**DETAILS,'allow_backorder':True},[(self.part.sku,qty)],uuid.uuid4())
    def action(self,o,action,**kw): return transition_order(self.wh,o.pk,action,**kw)
    def quarantine(self): return Stock.objects.create(part=self.part,location=Location.objects.create(code='Q1'),kind='OVERSTOCK',on_hand=0,held=True)
    def shipped(self):
        o=self.order();l=o.lines.get();self.action(o,'pick');self.action(o,'pack',line_id=l.pk,amount=6,scan=self.part.barcode);self.action(o,'ship',carrier='UPS',tracking='000123');return o,l
    def test_backorder_partial_ship_replenish_and_finish(self):
        o=self.order(15);l=o.lines.get();self.assertEqual(l.backordered,5)
        self.action(o,'pick',line_id=l.pk,amount=4);l.refresh_from_db();self.assertEqual(l.staged,4)
        self.action(o,'pack',line_id=l.pk,amount=4,scan='0012');self.action(o,'ship',carrier='UPS',tracking='001')
        o.refresh_from_db();self.assertEqual(o.status,'PARTIAL');l.refresh_from_db();self.assertEqual(l.staged,0)
        update_stock(self.wh,self.stock.pk,'receive',5,'R1')
        self.action(o,'allocate');l.refresh_from_db();self.assertEqual(l.backordered,0)
        self.action(o,'pick');self.action(o,'pack',line_id=l.pk,amount=11,scan='0099');self.action(o,'ship',carrier='UPS',tracking='002')
        l.refresh_from_db();o.refresh_from_db();self.assertEqual(o.status,'SHIPPED');self.assertEqual(l.shipped,15);self.assertEqual(o.shipments.count(),2)
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,0);self.assertEqual(self.stock.reserved,0)
    def test_stock_alert_uses_available_and_excludes_unknown_held_staged(self):
        self.order(6)
        s=self.quarantine();s.on_hand=100;s.save()
        Stock.objects.create(part=self.part,location=Location.objects.create(code='U'),kind='PICK',on_hand=99,count_required=True)
        r=stock_summary()[0];self.assertTrue(r['low']);self.assertEqual(r['available'],4);self.assertEqual(r['unknown'],1);self.assertEqual(r['reorder'],16)
        o=Order.objects.get();self.action(o,'pick');r=stock_summary()[0];self.assertEqual(r['available'],4);self.assertEqual(r['staged'],6)
    def test_no_threshold_is_not_zero_and_zero_threshold_alerts(self):
        self.part.low_stock_threshold=None;self.part.save();self.assertFalse(stock_summary()[0]['low'])
        self.part.low_stock_threshold=0;self.part.save();self.order(10);self.assertTrue(stock_summary()[0]['low'])
    def test_hold_blocks_reservation_transfer_and_requires_unreserved(self):
        set_hold(self.wh,self.stock.pk,True,'Damaged')
        o=self.order(3);self.assertEqual(o.lines.get().backordered,3)
        q=self.quarantine()
        with self.assertRaises(WorkflowError): transfer_stock(self.wh,self.stock.pk,q.pk,1,'Move')
        set_hold(self.wh,self.stock.pk,False,'Inspected');self.action(o,'allocate')
        with self.assertRaises(WorkflowError): set_hold(self.wh,self.stock.pk,True,'Damage')
    def test_packing_requires_matching_scan_and_within_pick(self):
        o=self.order();l=o.lines.get();self.action(o,'pick')
        for scan,amount in [('WRONG',6),('0012',7)]:
            with self.assertRaises(WorkflowError): self.action(o,'pack',line_id=l.pk,amount=amount,scan=scan)
        with self.assertRaises(WorkflowError): self.action(o,'ship',carrier='UPS',tracking='x')
        l.refresh_from_db();self.assertEqual(l.packed,0)
    def test_duplicate_pick_pack_and_ship_keys(self):
        o=self.order();l=o.lines.get()
        for action,kw in [('pick',dict(line_id=l.pk,amount=3)),('pack',dict(line_id=l.pk,amount=3,scan='0012')),('ship',dict(carrier='UPS',tracking='001'))]:
            key=uuid.uuid4();self.action(o,action,key=key,**kw);self.action(o,action,key=key,**kw)
        l.refresh_from_db();self.assertEqual((l.picked,l.packed,l.shipped),(3,3,3));self.assertEqual(Shipment.objects.count(),1)
    def test_bad_action_rolls_back_key_and_stock(self):
        o=self.order();key=uuid.uuid4()
        with self.assertRaises(WorkflowError): self.action(o,'ship',key=key)
        self.action(o,'pick',key=key);self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,4)
    def test_return_is_bounded_quarantined_and_idempotent(self):
        o,l=self.shipped();q=self.quarantine();key=uuid.uuid4()
        receive_return(self.wh,l.pk,q.pk,2,'Damaged',key);receive_return(self.wh,l.pk,q.pk,2,'Damaged',key)
        q.refresh_from_db();self.assertEqual(q.on_hand,2);self.assertEqual(ReturnReceipt.objects.count(),1)
        with self.assertRaises(WorkflowError): receive_return(self.wh,l.pk,q.pk,5,'Excess',uuid.uuid4())
        with self.assertRaises(WorkflowError): receive_return(self.wh,l.pk,self.stock.pk,1,'Wrong bin',uuid.uuid4())
        l.refresh_from_db();self.assertEqual(l.returned,2);self.assertEqual(stock_summary()[0]['available'],4)
    def test_cycle_count_due_resets_only_on_count(self):
        self.assertEqual(len(due_counts()),1)
        update_stock(self.wh,self.stock.pk,'count',10,'Count');self.assertEqual(len(due_counts()),0)
        self.stock.refresh_from_db();old=self.stock.last_counted_at
        update_stock(self.wh,self.stock.pk,'receive',2,'Receipt');self.stock.refresh_from_db();self.assertEqual(self.stock.last_counted_at,old)
        self.stock.last_counted_at=timezone.now()-timedelta(days=91);self.stock.save();self.assertEqual(len(due_counts()),1)
    def test_daily_digest_single_recipient_opt_in_and_dedupe(self):
        self.order();AlertSubscription.objects.create(user=self.fd,enabled=True)
        AlertSubscription.objects.create(user=self.wh,enabled=False)
        self.assertEqual(send_stock_digest()['sent'],1);self.assertEqual(send_stock_digest()['sent'],0)
        self.assertEqual(len(mail.outbox),1);self.assertEqual(mail.outbox[0].to,['fd@example.com'])
        self.assertIn('Available: 4',mail.outbox[0].body)
    def test_digest_disabled_dry_run_and_recovered_stock(self):
        AlertSubscription.objects.create(user=self.fd,enabled=True);o=self.order()
        with override_settings(WMS_EMAIL_ENABLED=False): self.assertTrue(send_stock_digest()['disabled'])
        self.assertIn('preview',send_stock_digest(dry_run=True));self.assertEqual(AlertDelivery.objects.count(),0)
        self.action(o,'cancel');self.assertEqual(send_stock_digest()['sent'],0)
    def test_digest_failure_recorded_not_silently_repeated(self):
        self.order();AlertSubscription.objects.create(user=self.fd,enabled=True)
        with patch('warehouse.notifications.EmailMessage.send',side_effect=RuntimeError('password should not be logged')):
            self.assertEqual(send_stock_digest()['failed'],1)
        d=AlertDelivery.objects.get();self.assertEqual(d.status,'FAILED');self.assertEqual(d.error,'RuntimeError')
        self.assertEqual(send_stock_digest()['sent'],0)
    def test_inactive_and_revoked_users_not_emailed(self):
        self.order();AlertSubscription.objects.create(user=self.fd,enabled=True);self.fd.groups.clear()
        self.assertEqual(send_stock_digest()['sent'],0)
    def test_portal_roles_forms_and_exports(self):
        o=self.order();self.client.force_login(self.fd)
        for url in ['/alerts/','/inventory/export/',f'/orders/{o.pk}/print/']: self.assertEqual(self.client.get(url).status_code,200,url)
        for url in ['/work/','/bins/new/',f'/parts/{self.part.pk}/']: self.assertEqual(self.client.get(url).status_code,403,url)
        self.assertEqual(self.client.post(f'/inventory/{self.stock.pk}/hold/',{'held':'1','note':'x'}).status_code,403)
        self.client.force_login(self.wh)
        for url in ['/work/','/bins/new/',f'/parts/{self.part.pk}/',f'/orders/{o.pk}/']: self.assertEqual(self.client.get(url).status_code,200,url)
        res=self.client.post('/bins/new/',dict(sku='0012',location='Q2',kind='OVERSTOCK',opening_count=0,held='on',note='Returns'))
        self.assertEqual(res.status_code,302);self.assertTrue(Stock.objects.get(location__code='Q2').held)
    def test_negative_threshold_and_invalid_target_rejected(self):
        self.client.force_login(self.wh)
        data=dict(description='',barcode='',unit='EA',low_stock_threshold='-1',target_stock=0,pick_min=0,count_interval_days=90)
        self.assertContains(self.client.post(f'/parts/{self.part.pk}/',data),'Use zero or a positive quantity')
        data['low_stock_threshold']=5;self.assertContains(self.client.post(f'/parts/{self.part.pk}/',data),'Target must be at least')
    def test_csv_formula_is_escaped(self):
        self.part.description='=HYPERLINK("bad")';self.part.save();self.client.force_login(self.fd)
        self.assertIn("'=HYPERLINK",self.client.get('/inventory/export/').content.decode())
    def test_replenishment_for_reserved_overstock(self):
        over=Stock.objects.create(part=self.part,location=Location.objects.create(code='O'),kind='OVERSTOCK',on_hand=20)
        o=self.order(25);self.assertEqual(stock_summary()[0]['replenish'],15)
        transfer_stock(self.wh,over.pk,self.stock.pk,20,'Replenish');self.action(o,'pick')
        self.assertEqual(o.lines.get().picked,25)
    def test_unpack_unpick_then_cancel_preserves_inventory(self):
        o=self.order();l=o.lines.get();self.action(o,'pick');self.action(o,'pack',line_id=l.pk,amount=6,scan='0012')
        with self.assertRaises(WorkflowError): self.action(o,'unpick',line_id=l.pk,amount=1,note='Correction')
        self.action(o,'unpack',line_id=l.pk,amount=6,note='Packing correction')
        self.action(o,'unpick',line_id=l.pk,amount=6,note='Returned to P1')
        self.action(o,'cancel');self.stock.refresh_from_db();l.refresh_from_db()
        self.assertEqual(self.stock.on_hand,10);self.assertEqual(self.stock.reserved,0);self.assertEqual(l.cancelled,6)
    def test_cancel_remaining_after_partial_ship(self):
        o=self.order(20);l=o.lines.get();self.action(o,'pick',line_id=l.pk,amount=4)
        self.action(o,'pack',line_id=l.pk,amount=4,scan='0012');self.action(o,'ship',carrier='UPS',tracking='003')
        self.action(o,'cancel_remaining',note='Customer cancelled balance')
        l.refresh_from_db();o.refresh_from_db();self.stock.refresh_from_db()
        self.assertEqual(l.cancelled,16);self.assertEqual(l.backordered,0);self.assertEqual(o.status,'SHIPPED');self.assertEqual(self.stock.reserved,0)
    def test_receipt_and_transfer_retries_do_not_duplicate(self):
        key=uuid.uuid4()
        for _ in range(2): update_stock(self.wh,self.stock.pk,'receive',2,'R1',key=key)
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,12)
        other=Stock.objects.create(part=self.part,location=Location.objects.create(code='P2'),kind='PICK',on_hand=0)
        key=uuid.uuid4()
        for _ in range(2): transfer_stock(self.wh,self.stock.pk,other.pk,3,'Move',key=key)
        other.refresh_from_db();self.stock.refresh_from_db();self.assertEqual(other.on_hand,3);self.assertEqual(self.stock.on_hand,9)
    def test_alert_preference_is_only_for_current_user(self):
        self.client.force_login(self.fd)
        self.client.post('/alerts/',{'enabled':'on','user':self.wh.pk})
        self.assertTrue(AlertSubscription.objects.get(user=self.fd).enabled)
        self.assertFalse(AlertSubscription.objects.filter(user=self.wh).exists())
