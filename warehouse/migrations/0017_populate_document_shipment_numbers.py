from decimal import Decimal
from django.db import migrations


def number_existing(apps, schema_editor):
    """Preserve balances and dispatch history; never infer historic carrier references."""
    alias = schema_editor.connection.alias
    Order = apps.get_model("warehouse", "Order")
    Req = apps.get_model("warehouse", "Requisition")
    Row = apps.get_model("warehouse", "RequisitionLine")
    Allocation = apps.get_model("warehouse", "Allocation")
    Sequence = apps.get_model("warehouse", "DocumentSequence")
    last_document = 0
    for order in Order.objects.using(alias).order_by("created_at", "pk").iterator():
        last_document += 1
        document = f"FD{last_document:06d}"
        shipment_number = 0

        def create_req(created_at):
            nonlocal shipment_number
            shipment_number += 1
            req = Req.objects.using(alias).create(
                order_id=order.pk,
                shipment_number=shipment_number,
                reference=f"{document} {shipment_number:03d}",
            )
            Req.objects.using(alias).filter(pk=req.pk).update(created_at=created_at)
            return req

        shipped_totals = {}
        for shipment in order.shipments.using(alias).order_by("created_at", "pk"):
            req = create_req(shipment.created_at)
            shipment.requisition_id = req.pk
            shipment.save(using=alias, update_fields=["requisition"])
            quantities = {}
            for line in shipment.lines.using(alias).select_related("line"):
                if line.line.order_id != order.pk or line.quantity <= 0:
                    raise RuntimeError(
                        f"Shipment history needs review for order {order.pk}."
                    )
                quantities[line.line_id] = (
                    quantities.get(line.line_id, Decimal(0)) + line.quantity
                )
            for line_id, qty in quantities.items():
                Row.objects.using(alias).create(
                    requisition_id=req.pk,
                    line_id=line_id,
                    quantity=qty,
                    picked=qty,
                    packed=qty,
                    shipped=qty,
                )
                shipped_totals[line_id] = shipped_totals.get(line_id, Decimal(0)) + qty

        open_req = None
        for line in order.lines.using(alias).all():
            if shipped_totals.get(line.pk, 0) != line.shipped:
                raise RuntimeError(f"Shipment totals need review for order {order.pk}.")
            allocations = Allocation.objects.using(alias).filter(line_id=line.pk)
            reserved = sum(
                (a.quantity for a in allocations.filter(state="RESERVED")), Decimal(0)
            )
            picked = line.picked - line.shipped
            packed = line.packed - line.shipped
            quantity = reserved + picked
            if quantity > line.quantity - line.cancelled - line.shipped:
                raise RuntimeError(
                    f"Reservation totals need review for order {order.pk}."
                )
            if not quantity:
                continue
            if open_req is None:
                open_req = create_req(order.created_at)
            Row.objects.using(alias).create(
                requisition_id=open_req.pk,
                line_id=line.pk,
                quantity=quantity,
                picked=picked,
                packed=packed,
            )
            allocations.filter(state="RESERVED").update(requisition_id=open_req.pk)
            # Old allocations do not identify their shipment. Assign only unshipped
            # picked quantities, in the same newest-first order used by legacy unpick.
            for allocation in allocations.filter(state="PICKED").order_by("-pk"):
                take = min(picked, allocation.quantity)
                if not take:
                    break
                if take == allocation.quantity:
                    allocation.requisition_id = open_req.pk
                    allocation.save(using=alias, update_fields=["requisition"])
                else:
                    allocation.quantity -= take
                    allocation.save(using=alias, update_fields=["quantity"])
                    Allocation.objects.using(alias).create(
                        line_id=line.pk,
                        stock_id=allocation.stock_id,
                        quantity=take,
                        state="PICKED",
                        release_id=allocation.release_id,
                        requisition_id=open_req.pk,
                    )
                picked -= take
            if picked:
                raise RuntimeError(f"Pick history needs review for order {order.pk}.")
        if not shipment_number:
            create_req(order.created_at)
        Order.objects.using(alias).filter(pk=order.pk).update(
            document_number=document,
            next_shipment_number=shipment_number + 1,
        )
    Sequence.objects.using(alias).update_or_create(
        name="FD_DOCUMENT",
        defaults={"value": last_document},
    )


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0016_document_shipment_numbering")]
    operations = [migrations.RunPython(number_existing, migrations.RunPython.noop)]
