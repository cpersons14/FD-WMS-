import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,Client,override_settings,TransactionTestCase
from django.utils import timezone
from .models import (Part,Stock,Location,InboundReceipt,ReceiptGroup,ReceiptDocument,
                     ReceiptEmail,ReceiptEmailAttempt,Event)
from .receiving import save_receipt,post_receipt,send_receipt_email,read_uploads
from .services import WorkflowError,submit_order,transition_order,transfer_stock,update_stock
from .units import convert
from .tests import DETAILS

SETTINGS=dict(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}},AXES_ENABLED=False,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='test@example.com',WMS_RECEIPT_EMAIL_ENABLED=True)

@override_settings(**SETTINGS)
class ReceivingTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.wh=U.objects.create_user('office');self.wh.groups.add(Group.objects.get_or_create(name='S3PL Warehouse')[0])
        self.fd=U.objects.create_user('fd');self.fd.groups.add(Group.objects.get_or_create(name='FD Corporate')[0])
        self.group=ReceiptGroup.objects.create(name='Pilot FD',addresses='receiving@example.com\ncopy@example.com')
        self.part=Part.objects.create(sku='0012',description='Shelf',unit='EA',units_per_case=12,cases_per_pallet=20)
        self.stock=Stock.objects.create(part=self.part,location=Location.objects.create(code='P1'),kind='PICK',on_hand=10)
        self.header=dict(supplier='Supplier',received_date=timezone.localdate(),reference='BOL001',group=self.group,notes='No shortage')
    def row(self,**kw):
        return dict(dict(sku='0012',location='P1',quantity='2',unit='CASE',loose_eaches='1',case_pack='12',pallet_cases='20',note='',damaged=False),**kw)
    def draft(self,rows=None,**kwargs):
        return save_receipt(self.wh,uuid.uuid4(),None,0,self.header,rows if rows is not None else [self.row()],**kwargs)
    def posted(self,**kw):
        r=self.draft(**kw);return post_receipt(self.wh,r.pk,r.revision)
    def pdf(self,name='bol.pdf'): return SimpleUploadedFile(name,b'%PDF-1.4\n%%EOF',content_type='application/pdf')
    def test_draft_and_clarification_do_not_change_stock_or_email(self):
        r=self.draft(rows=[self.row(quantity='')],status='CLARIFY')
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,10)
        self.assertEqual(r.status,'CLARIFY');self.assertEqual(ReceiptEmail.objects.count(),0)
        with self.assertRaises(WorkflowError): post_receipt(self.wh,r.pk,r.revision)
    def test_multirow_delivery_posts_once_and_keeps_conversion_audit(self):
        r=self.posted(rows=[self.row(),self.row(quantity='1',unit='PALLET',loose_eaches='0')])
        post_receipt(self.wh,r.pk,0)
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,275)
        self.assertEqual(r.lines.count(),2);self.assertEqual(ReceiptEmail.objects.count(),1)
        self.assertEqual(Event.objects.filter(receipt=r,action='RECEIVE').count(),2)
        self.assertEqual(r.lines.first().entry['factor'],'12')
    def test_failure_on_later_row_rolls_back_all_posting(self):
        unknown=Stock.objects.create(part=self.part,location=Location.objects.create(code='U1'),kind='OVERSTOCK',on_hand=None,count_required=True)
        r=self.draft(rows=[self.row(),self.row(location='U1')])
        with self.assertRaises(WorkflowError): post_receipt(self.wh,r.pk,r.revision)
        self.stock.refresh_from_db();r.refresh_from_db()
        self.assertEqual(self.stock.on_hand,10);self.assertEqual(r.lines.count(),0)
        self.assertEqual(r.status,'DRAFT');self.assertFalse(ReceiptEmail.objects.exists())
    def test_case_pack_change_requires_review_and_posted_snapshot_stays(self):
        r=self.draft();self.part.units_per_case=10;self.part.save()
        with self.assertRaises(WorkflowError): post_receipt(self.wh,r.pk,r.revision)
        r=save_receipt(self.wh,r.request_key,r.pk,r.revision,self.header,[self.row(case_pack='10')])
        r=post_receipt(self.wh,r.pk,r.revision)
        self.part.units_per_case=6;self.part.save();self.assertEqual(r.lines.get().quantity,21)
        self.assertEqual(r.lines.get().entry['case_pack'],10)
    def test_damaged_receipt_requires_held_bin(self):
        r=self.draft(rows=[self.row(damaged=True)])
        with self.assertRaises(WorkflowError): post_receipt(self.wh,r.pk,r.revision)
        self.stock.held=True;self.stock.save();r=post_receipt(self.wh,r.pk,r.revision)
        self.stock.refresh_from_db();self.assertEqual(self.stock.available,0);self.assertIn('ON HOLD',r.email.body)
    def test_stale_draft_and_duplicate_creation_are_protected(self):
        r=self.draft();again=save_receipt(self.wh,r.request_key,None,0,self.header,[])
        self.assertEqual(again.pk,r.pk);self.assertEqual(InboundReceipt.objects.count(),1)
        with self.assertRaises(WorkflowError): save_receipt(self.wh,r.request_key,r.pk,0,self.header,[])
        self.assertEqual(InboundReceipt.objects.get(pk=r.pk).draft_lines,[self.row()])
    def test_posted_receipt_cannot_be_edited(self):
        r=self.posted();again=save_receipt(self.wh,r.request_key,r.pk,0,{**self.header,'reference':'Changed'},[])
        self.assertEqual(again.reference,'BOL001');self.assertEqual(again.lines.count(),1)
    def test_group_and_header_validation_block_posting_without_stock_change(self):
        for header in [{**self.header,'supplier':''},{**self.header,'received_date':timezone.localdate()+timedelta(days=1)},{**self.header,'group':None}]:
            r=save_receipt(self.wh,uuid.uuid4(),None,0,header,[self.row()])
            with self.assertRaises(WorkflowError): post_receipt(self.wh,r.pk,r.revision)
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,10)
    def test_empty_recipient_group_blocks_submit_even_when_mail_disabled(self):
        self.group.addresses='';self.group.save();r=self.draft()
        with self.assertRaises(WorkflowError): post_receipt(self.wh,r.pk,r.revision)
    def test_attachments_retained_selected_and_sent_as_one_delivery(self):
        files=read_uploads([self.pdf(),self.pdf('internal.pdf')])
        r=self.posted(uploads=files,new_email_indices=['0'])
        self.assertEqual(r.documents.count(),2)
        self.assertEqual(send_receipt_email(r.pk,self.wh),'SENT')
        self.assertEqual(send_receipt_email(r.pk,self.wh),'SENT')
        self.assertEqual(len(mail.outbox),1);self.assertEqual(len(mail.outbox[0].attachments),1)
        self.assertEqual(mail.outbox[0].attachments[0].filename,'bol.pdf')
        self.assertCountEqual(mail.outbox[0].to,['receiving@example.com','copy@example.com'])
        self.assertIn('= 25 EA',mail.outbox[0].body)
    def test_email_disabled_leaves_pending_without_attempt(self):
        r=self.posted()
        with override_settings(WMS_RECEIPT_EMAIL_ENABLED=False): self.assertEqual(send_receipt_email(r.pk),'DISABLED')
        self.assertFalse(ReceiptEmailAttempt.objects.exists());self.assertEqual(r.email.status,'PENDING')
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,35)
    def test_email_failure_retry_never_reposts_and_requires_delivery_review(self):
        r=self.posted()
        with patch('warehouse.receiving.EmailMessage.send',side_effect=RuntimeError('secret must not be recorded')):
            self.assertEqual(send_receipt_email(r.pk,self.wh),'FAILED')
        self.assertEqual(send_receipt_email(r.pk),'FAILED')
        self.assertEqual(send_receipt_email(r.pk,self.wh,retry=True),'FAILED')
        self.assertEqual(send_receipt_email(r.pk,self.wh,retry=True,checked_delivery=True),'SENT')
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,35)
        self.assertEqual(r.email.attempts.count(),2);self.assertEqual(r.email.attempts.last().error,'RuntimeError')
    def test_interrupted_send_not_automatically_retried(self):
        r=self.posted();ReceiptEmail.objects.filter(receipt=r).update(status='SENDING')
        attempt=ReceiptEmailAttempt.objects.create(email=r.email)
        self.assertEqual(send_receipt_email(r.pk),'SENDING')
        with self.assertRaises(WorkflowError): send_receipt_email(r.pk,self.wh,retry=True,checked_delivery=True)
        ReceiptEmailAttempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now()-timedelta(minutes=6))
        self.assertEqual(send_receipt_email(r.pk,self.wh,retry=True,checked_delivery=True),'SENT')
    def test_revoked_group_addresses_do_not_receive_queued_email(self):
        r=self.posted();self.group.addresses='replacement@example.com';self.group.save()
        self.assertEqual(send_receipt_email(r.pk),'GROUP_REVIEW_REQUIRED');self.assertFalse(ReceiptEmailAttempt.objects.exists())
    def test_upload_rejects_wrong_type_and_size(self):
        for f in [SimpleUploadedFile('x.html',b'<script>bad</script>'),SimpleUploadedFile('fake.pdf',b'not pdf'),SimpleUploadedFile('big.pdf',b'%PDF-'+b'0'*(6*1024*1024))]:
            with self.assertRaises(WorkflowError): read_uploads([f])
    def test_foreign_attachment_selection_rejected_and_remove_is_draft_only(self):
        r=self.draft(uploads=read_uploads([self.pdf()]),new_email_indices=['0']);doc=r.documents.get()
        other=self.draft()
        with self.assertRaises(WorkflowError): save_receipt(self.wh,other.request_key,other.pk,other.revision,self.header,[],keep_ids=[str(doc.pk)])
        r=save_receipt(self.wh,r.request_key,r.pk,r.revision,self.header,[self.row()],keep_ids=[])
        self.assertEqual(r.documents.count(),0)
    def test_customer_and_anonymous_cannot_enter_receiving_or_fetch_documents(self):
        r=self.posted(uploads=read_uploads([self.pdf()]),new_email_indices=['0']);doc=r.documents.get()
        urls=['/receipts/','/receipts/new/',f'/receipts/{r.pk}/',f'/receipt-documents/{doc.pk}/']
        for url in urls:self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.fd)
        for url in urls:self.assertEqual(self.client.get(url).status_code,403)
        with self.assertRaises(PermissionError): post_receipt(self.fd,r.pk,r.revision)
        with self.assertRaises(PermissionError): send_receipt_email(r.pk,self.fd)
        c=Client(enforce_csrf_checks=True);c.force_login(self.wh)
        self.assertEqual(c.post('/receipts/new/',{}).status_code,403)
    def test_office_pages_document_headers_and_submit_from_form(self):
        self.client.force_login(self.wh)
        self.assertEqual(self.client.get('/receipts/new/').status_code,200)
        row=self.row();data={**self.header,'group':self.group.pk,'received_date':str(timezone.localdate()),'request_key':str(uuid.uuid4()),'revision':'0','action':'submit',**{k:str(v) if k!='damaged' else '0' for k,v in row.items()},'documents':self.pdf(),'new_email':['0']}
        response=self.client.post('/receipts/new/',data)
        self.assertEqual(response.status_code,302)
        r=InboundReceipt.objects.get();self.assertEqual(r.status,'POSTED');self.assertEqual(r.email.status,'SENT')
        self.assertContains(self.client.get(response.url),'Receiving confirmation')
        self.assertContains(self.client.get('/receipts/?q=BOL001'),'Supplier')
        doc=r.documents.get();res=self.client.get(f'/receipt-documents/{doc.pk}/')
        self.assertEqual(res['Content-Type'],'application/pdf');self.assertIn('sandbox',res['Content-Security-Policy'])
        self.assertIn('no-store',res['Cache-Control']);self.assertEqual(bytes(doc.content),res.content)
    def test_submit_error_preserves_draft_and_upload(self):
        self.client.force_login(self.wh);row=self.row(quantity='')
        data={**self.header,'group':self.group.pk,'received_date':str(timezone.localdate()),'request_key':str(uuid.uuid4()),'revision':'0','action':'submit',**{k:str(v) if k!='damaged' else '0' for k,v in row.items()},'documents':self.pdf(),'new_email':['0']}
        response=self.client.post('/receipts/new/',data,follow=True)
        self.assertContains(response,'Draft saved; inventory has not posted')
        self.assertEqual(InboundReceipt.objects.get().status,'DRAFT');self.assertEqual(ReceiptDocument.objects.count(),1)


@override_settings(**SETTINGS)
class CasePackTests(TestCase):
    def setUp(self):
        self.wh=get_user_model().objects.create_superuser('tester')
        self.part=Part.objects.create(sku='P',unit='EA',units_per_case=12,cases_per_pallet=10)
        self.stock=Stock.objects.create(part=self.part,location=Location.objects.create(code='P'),kind='PICK',on_hand=500)
    def test_eaches_cases_pallets_and_partial_pack(self):
        for qty,unit,loose,want in [(3,'EA',0,3),(2,'CASE',3,27),(2,'PALLET',4,244),(0,'CASE',3,3)]:
            self.assertEqual(convert(self.part,qty,unit,loose)[0],want)
    def test_missing_pack_unverified_and_fractional_quantities_blocked(self):
        for qty,unit in [(0,'EA'),('1.5','CASE'),('0.5','EA'),('NaN','PALLET')]:
            with self.assertRaises(WorkflowError): convert(self.part,qty,unit)
        self.part.units_per_case=None
        with self.assertRaises(WorkflowError): convert(self.part,1,'CASE')
        self.part.unit=''
        with self.assertRaises(WorkflowError): convert(self.part,1,'EA')
    def test_mixed_units_merge_and_order_snapshot_survives_master_change(self):
        o=submit_order(self.wh,DETAILS,[('P',2,'CASE'),('P',1,'EA')],uuid.uuid4());line=o.lines.get()
        self.assertEqual(line.quantity,25)
        self.part.units_per_case=6;self.part.save()
        transition_order(self.wh,o.pk,'pick',line_id=line.pk,amount=2,unit='CASE')
        transition_order(self.wh,o.pk,'pack',line_id=line.pk,amount=2,unit='CASE',scan='P')
        line.refresh_from_db();self.assertEqual(line.picked,24);self.assertEqual(line.packed,24)
    def test_transfers_and_counts_use_confirmed_units(self):
        other=Stock.objects.create(part=self.part,location=Location.objects.create(code='O'),kind='OVERSTOCK',on_hand=0)
        transfer_stock(self.wh,self.stock.pk,other.pk,1,'Move',unit='PALLET',loose_eaches=2)
        other.refresh_from_db();self.assertEqual(other.on_hand,122)
        update_stock(self.wh,other.pk,'count',3,'Count',unit='CASE',loose_eaches=1)
        other.refresh_from_db();self.assertEqual(other.on_hand,37)
    def test_confirmation_never_rescales_original_stock(self):
        self.part.unit='';self.part.units_per_case=None;self.part.cases_per_pallet=None;self.part.save()
        self.client.force_login(self.wh)
        data=dict(description='',barcode='',unit='EA',units_per_case=12,cases_per_pallet=10,count_interval_days=90)
        self.assertContains(self.client.post(f'/parts/{self.part.pk}/',data),'Verify the existing quantity basis')
        data['confirm_eaches']='on';self.assertEqual(self.client.post(f'/parts/{self.part.pk}/',data).status_code,302)
        self.stock.refresh_from_db();self.assertEqual(self.stock.on_hand,500)


class ReceiptConcurrencyTests(TransactionTestCase):
    def test_simultaneous_post_of_one_delivery_changes_stock_once(self):
        from django.db import connection,close_old_connections
        if connection.vendor!='postgresql': self.skipTest('Requires PostgreSQL row locks; run in staging.')
        import threading
        from concurrent.futures import ThreadPoolExecutor
        user=get_user_model().objects.create_superuser('concurrent-office')
        p=Part.objects.create(sku='R',unit='EA')
        Stock.objects.create(part=p,location=Location.objects.create(code='R'),kind='PICK',on_hand=0)
        g=ReceiptGroup.objects.create(name='Test',addresses='test@example.com')
        r=save_receipt(user,uuid.uuid4(),None,0,dict(supplier='S',received_date=timezone.localdate(),reference='R',group=g,notes=''),[dict(sku='R',location='R',quantity='3',unit='EA',loose_eaches='0',case_pack='',pallet_cases='',note='',damaged=False)])
        barrier=threading.Barrier(2)
        def post():
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=user.pk);barrier.wait(timeout=10)
                return post_receipt(actor,r.pk,r.revision).pk
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:post(),range(2)))
        self.assertEqual(results,[r.pk,r.pk]);self.assertEqual(Stock.objects.get(part=p).on_hand,3)
        self.assertEqual(ReceiptEmail.objects.count(),1)
