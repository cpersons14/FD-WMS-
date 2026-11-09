from django.db import migrations


def preserve(apps,schema_editor):
    Line=apps.get_model('warehouse','OrderLine')
    Allocation=apps.get_model('warehouse','Allocation')
    Order=apps.get_model('warehouse','Order')
    Shipment=apps.get_model('warehouse','Shipment')
    ShipmentLine=apps.get_model('warehouse','ShipmentLine')
    import uuid
    for order in Order.objects.filter(status__in=['PICKED','SHIPPED','CANCELLED']):
        lines=Line.objects.filter(order_id=order.pk)
        if order.status=='CANCELLED':
            Allocation.objects.filter(line__order_id=order.pk).update(state='RELEASED')
            continue
        Allocation.objects.filter(line__order_id=order.pk).update(state='PICKED')
        shipment=None
        if order.status=='SHIPPED':
            shipment=Shipment.objects.create(order_id=order.pk,actor_id=order.created_by_id,request_key=uuid.uuid4(),carrier=order.carrier,tracking=order.tracking)
            if order.shipped_at: Shipment.objects.filter(pk=shipment.pk).update(created_at=order.shipped_at)
        for line in lines:
            line.picked=line.quantity
            if shipment:
                line.packed=line.quantity;line.shipped=line.quantity
                ShipmentLine.objects.create(shipment=shipment,line=line,quantity=line.quantity)
            line.save()


class Migration(migrations.Migration):
    dependencies=[('warehouse','0003_alertdelivery_alertsubscription_operationkey_and_more')]
    operations=[migrations.RunPython(preserve,migrations.RunPython.noop)]
