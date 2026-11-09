import uuid
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from .tests import DETAILS

class UpgradeTests(TransactionTestCase):
    def test_existing_picked_shipped_and_cancelled_orders_survive_upgrade(self):
        old=('warehouse','0002_stock_count_required')
        latest=MigrationExecutor(connection).loader.graph.leaf_nodes('warehouse')[0]
        executor=MigrationExecutor(connection)
        executor.migrate([old])
        apps=executor.loader.project_state([old]).apps
        try:
            U=apps.get_model('auth','User');u=U.objects.create(username='legacy')
            P=apps.get_model('warehouse','Part');p=P.objects.create(sku='LEGACY')
            L=apps.get_model('warehouse','Location');loc=L.objects.create(code='P')
            S=apps.get_model('warehouse','Stock');stock=S.objects.create(part=p,location=loc,kind='PICK',on_hand=20)
            O=apps.get_model('warehouse','Order');Line=apps.get_model('warehouse','OrderLine');A=apps.get_model('warehouse','Allocation')
            ids={}
            for status in ['PICKED','SHIPPED','CANCELLED']:
                o=O.objects.create(created_by=u,request_key=uuid.uuid4(),status=status,carrier='UPS' if status=='SHIPPED' else '',tracking='001' if status=='SHIPPED' else '',**DETAILS)
                line=Line.objects.create(order=o,part=p,quantity=3);A.objects.create(line=line,stock=stock,quantity=3);ids[status]=o.pk
        finally:
            executor=MigrationExecutor(connection);executor.migrate([latest])
        from .models import Order,Stock
        self.assertEqual(Order.objects.get(pk=ids['PICKED']).lines.get().staged,3)
        shipped=Order.objects.get(pk=ids['SHIPPED']);self.assertEqual(shipped.lines.get().shipped,3);self.assertEqual(shipped.shipments.count(),1)
        self.assertEqual(shipped.lines.get().allocations.get().state,'PICKED')
        cancelled=Order.objects.get(pk=ids['CANCELLED']);self.assertEqual(cancelled.lines.get().cancelled,3);self.assertEqual(cancelled.lines.get().allocations.get().state,'RELEASED')
        self.assertEqual(Stock.objects.get(pk=stock.pk).on_hand,20)
