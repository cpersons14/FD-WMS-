import uuid
from decimal import Decimal
from django.test import TestCase,Client,override_settings,TransactionTestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection,close_old_connections
from .models import Part,Location,Stock,Order,Event,Allocation
from .services import submit_order,transition_order,update_stock,transfer_stock,WorkflowError

DETAILS=dict(store='00123',recipient='Family Dollar',address='123 Main St',city='Mount Juliet',state='TN',postal_code='37122',reference='Pilot',notes='')

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}},AXES_ENABLED=False)
class WorkflowTests(TestCase):
    def setUp(self):
        self.corporate=get_user_model().objects.create_user('corporate',password='Test-only-long-password-56')
        self.corporate.groups.add(Group.objects.create(name='FD Corporate'))
        self.warehouse=get_user_model().objects.create_user('warehouse',password='Test-only-long-password-57')
        self.warehouse.groups.add(Group.objects.create(name='S3PL Warehouse'))
        self.part=Part.objects.create(sku='001401',description='Shelf')
        self.pick=Stock.objects.create(part=self.part,location=Location.objects.create(code='P1'),kind='PICK',on_hand=10)
        self.over=Stock.objects.create(part=self.part,location=Location.objects.create(code='O1'),kind='OVERSTOCK',on_hand=20)
        self.unknown=Stock.objects.create(part=self.part,location=Location.objects.create(code='P2'),kind='PICK',on_hand=None)
    def order(self,qty=5,key=None): return submit_order(self.corporate,DETAILS,[(self.part.sku,qty)],key or uuid.uuid4())
    def test_reservation_leaves_on_hand_and_repeated_submission_reserves_once(self):
        key=uuid.uuid4();o=self.order(key=key);again=self.order(key=key)
        self.pick.refresh_from_db();self.assertEqual(o.pk,again.pk);self.assertEqual(self.pick.on_hand,10);self.assertEqual(self.pick.reserved,5)
    def test_insufficient_stock_rolls_back_entire_order(self):
        with self.assertRaises(WorkflowError): self.order(31)
        self.assertEqual(Order.objects.count(),0);self.assertEqual(Event.objects.count(),0)
    def test_multiple_lines_roll_back_when_second_is_short(self):
        other=Part.objects.create(sku='OTHER')
        with self.assertRaises(WorkflowError): submit_order(self.corporate,DETAILS,[(self.part.sku,5),(other.sku,1)],uuid.uuid4())
        self.pick.refresh_from_db();self.assertEqual(self.pick.reserved,0);self.assertEqual(Order.objects.count(),0)
    def test_pick_ship_and_repeat_pick(self):
        o=self.order();transition_order(self.warehouse,o.pk,'pick')
        self.pick.refresh_from_db();self.assertEqual(self.pick.on_hand,5);self.assertEqual(self.pick.reserved,0)
        with self.assertRaises(WorkflowError): transition_order(self.warehouse,o.pk,'pick')
        with self.assertRaises(WorkflowError): transition_order(self.warehouse,o.pk,'ship')
        transition_order(self.warehouse,o.pk,'pack',line_id=o.lines.get().pk,amount=5,scan=self.part.sku)
        transition_order(self.warehouse,o.pk,'ship','UPS','0012345');o.refresh_from_db();self.assertEqual(o.tracking,'0012345')
    def test_cancel_releases_stock_without_removing_it(self):
        o=self.order();transition_order(self.warehouse,o.pk,'cancel');self.pick.refresh_from_db()
        self.assertEqual(self.pick.on_hand,10);self.assertEqual(self.pick.reserved,0)
    def test_uncounted_and_zero_are_distinct(self):
        with self.assertRaises(WorkflowError): update_stock(self.warehouse,self.unknown.pk,'receive',1,'Receipt')
        update_stock(self.warehouse,self.unknown.pk,'count',0,'Initial count');self.unknown.refresh_from_db();self.assertEqual(self.unknown.on_hand,0)
    def test_count_below_reservations_is_blocked(self):
        self.order()
        with self.assertRaises(WorkflowError): update_stock(self.warehouse,self.pick.pk,'count',4,'Count')
    def test_reserved_overstock_replenishment(self):
        o=self.order(25)
        with self.assertRaises(WorkflowError): transition_order(self.warehouse,o.pk,'pick')
        transfer_stock(self.warehouse,self.over.pk,self.pick.pk,20,'Replenishment')
        transition_order(self.warehouse,o.pk,'pick');self.pick.refresh_from_db();self.over.refresh_from_db()
        self.assertEqual(self.pick.on_hand,5);self.assertEqual(self.over.on_hand,0);self.assertEqual(self.pick.reserved,0)
    def test_transfer_validates_unknown_and_wrong_part(self):
        with self.assertRaises(WorkflowError): transfer_stock(self.warehouse,self.over.pk,self.unknown.pk,2,'Move')
        with self.assertRaises(WorkflowError): transfer_stock(self.warehouse,self.over.pk,self.pick.pk,21,'Move')
    def test_corporate_cannot_mutate_stock_or_fulfill(self):
        with self.assertRaises(PermissionError): update_stock(self.corporate,self.pick.pk,'count',100,'x')
        with self.assertRaises(PermissionError): transition_order(self.corporate,self.order().pk,'pick')
        self.client.force_login(self.corporate)
        self.assertEqual(self.client.get(f'/inventory/{self.pick.pk}/').status_code,403)
        self.assertEqual(self.client.get('/history/').status_code,403)
    def test_anonymous_no_access_and_no_group_no_access(self):
        self.assertEqual(self.client.get('/inventory/').status_code,302)
        other=get_user_model().objects.create_user('other');self.client.force_login(other)
        self.assertEqual(self.client.get('/inventory/').status_code,403)
    def test_pages_and_order_form(self):
        self.client.force_login(self.corporate)
        for url in ['/','/inventory/','/orders/','/orders/new/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
        data={**DETAILS,'priority':'NORMAL','sku':[self.part.sku],'quantity':['3'],'request_key':str(uuid.uuid4())}
        result=self.client.post('/orders/new/',data);self.assertEqual(result.status_code,302)
        self.assertEqual(self.client.get(result.url).status_code,200)
        self.client.force_login(self.warehouse)
        for url in ['/history/',f'/inventory/{self.pick.pk}/',f'/inventory/{self.pick.pk}/transfer/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
    def test_csrf_is_enforced(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.warehouse)
        self.assertEqual(c.post(f'/inventory/{self.pick.pk}/',{'mode':'count','amount':20,'note':'x'}).status_code,403)
    def test_invalid_quantity_rejected(self):
        for q in ['NaN','Infinity','-1','0','0.0001']:
            with self.assertRaises(WorkflowError): self.order(q)
    def test_placeholder_zero_stays_in_count_queue(self):
        self.unknown.on_hand=0;self.unknown.count_required=True;self.unknown.save()
        self.assertIsNone(self.unknown.available)
        self.client.force_login(self.warehouse)
        self.assertContains(self.client.get('/inventory/?counted=no'),'P2')
        with self.assertRaises(WorkflowError): update_stock(self.warehouse,self.unknown.pk,'receive',4,'Receipt')
        update_stock(self.warehouse,self.unknown.pk,'count',0,'Physically verified zero')
        self.unknown.refresh_from_db();self.assertFalse(self.unknown.count_required);self.assertEqual(self.unknown.available,0)

class ConcurrencyTests(TransactionTestCase):
    def test_last_units_cannot_be_reserved_twice(self):
        if connection.vendor!='postgresql': self.skipTest('Requires PostgreSQL row locks; run on staging PostgreSQL before launch.')
        import threading
        from concurrent.futures import ThreadPoolExecutor
        user=get_user_model().objects.create_user('concurrent')
        user.groups.add(Group.objects.create(name='FD Corporate'))
        part=Part.objects.create(sku='RACE');Stock.objects.create(part=part,location=Location.objects.create(code='RACE'),kind='PICK',on_hand=1)
        barrier=threading.Barrier(2)
        def reserve():
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=user.pk);barrier.wait(timeout=10)
                try: submit_order(actor,DETAILS,[('RACE',1)],uuid.uuid4());return 'ok'
                except WorkflowError: return 'short'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:reserve(),range(2)))
        self.assertCountEqual(results,['ok','short']);self.assertEqual(Stock.objects.get(part=part).reserved,1)

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class LoginAndImportTests(TestCase):
    def test_login_lockout(self):
        get_user_model().objects.create_user('locked',password='Long-test-password-984')
        for _ in range(5): response=self.client.post('/login/',{'username':'locked','password':'incorrect'})
        self.assertEqual(response.status_code,429)
        response=self.client.post('/login/',{'username':'locked','password':'Long-test-password-984'})
        self.assertEqual(response.status_code,429)
    def test_synthetic_inventory_import_and_reimport_protection(self):
        import json
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from django.core.management import call_command
        from django.core.management.base import CommandError
        get_user_model().objects.create_superuser('importer',password='Long-test-password-985')
        rows=[
            dict(row=1,sku='DEMO-001',description='Synthetic item',location='DEMO-P1',kind='PICK',on_hand=10),
            dict(row=2,sku='DEMO-001',description='Synthetic item',location='DEMO-P2',kind='PICK',on_hand=None),
            dict(row=3,sku='DEMO-001',description='Synthetic item',location='DEMO-O1',kind='OVERSTOCK',on_hand=0,count_required=True),
            dict(row=4,sku='DEMO-002',description='Synthetic item two',location='DEMO-P3',kind='PICK',on_hand=4),
        ]
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic_inventory.json';path.write_text(json.dumps(rows))
            call_command('import_fd',str(path),actor='importer',verbosity=0)
            self.assertEqual(Part.objects.count(),2);self.assertEqual(Stock.objects.count(),4)
            self.assertEqual(Stock.objects.filter(count_required=True).count(),2)
            placeholder=Stock.objects.get(part__sku='DEMO-001',location__code='DEMO-O1')
            self.assertEqual(placeholder.on_hand,0);self.assertTrue(placeholder.count_required)
            self.assertIsNone(Stock.objects.get(location__code='DEMO-P2').on_hand)
            self.assertEqual(Stock.objects.filter(kind='PICK').count(),3)
            with self.assertRaises(CommandError): call_command('import_fd',str(path),actor='importer')

    def test_setup_roles_is_idempotent_and_does_not_seed_inventory(self):
        from django.core.management import call_command
        from .models import ReceiptGroup
        for _ in range(2): call_command('setup_wms',verbosity=0)
        self.assertEqual(Group.objects.filter(name__in=['FD Corporate','S3PL Warehouse']).count(),2)
        self.assertEqual(ReceiptGroup.objects.filter(name='FD Receiving').count(),1)
        self.assertFalse(Part.objects.exists());self.assertFalse(Stock.objects.exists())
