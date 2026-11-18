"""Warehouse state transitions. All stock-changing paths serialize on order then part."""

import json
import uuid
from decimal import Decimal
from django.db import transaction
from .models import (
    Part,
    Stock,
    Order,
    OrderLine,
    Allocation,
    Event,
    Shipment,
    ShipmentLine,
    ReturnReceipt,
    OperationKey,
    RequisitionLine,
)
from .services import WorkflowError, warehouse_user
from .requisitions import (
    attach_allocations,
    select_requisition,
    release_line,
    carry_remainder,
)


def require_warehouse(actor):
    if not warehouse_user(actor):
        raise PermissionError("Warehouse access required.")


def once(actor, key, **payload):
    if not key:
        return False  # Service callers may opt out; all browser forms provide a key.
    fingerprint = json.dumps(payload, sort_keys=True, default=str)
    saved, created = OperationKey.objects.get_or_create(
        key=key, defaults={"actor": actor, "fingerprint": fingerprint}
    )
    if not created and (saved.actor_id != actor.pk or saved.fingerprint != fingerprint):
        raise WorkflowError(
            "Action key already used for a different action. Reload this page."
        )
    return not created


def refresh_status(order):
    lines = list(order.lines.all())
    if all(line.cancelled == line.quantity for line in lines):
        status = "CANCELLED"
    elif all(line.shipped + line.cancelled == line.quantity for line in lines):
        status = "SHIPPED"
    elif all(line.packed + line.cancelled == line.quantity for line in lines):
        status = "PACKED"
    elif all(line.picked + line.cancelled == line.quantity for line in lines):
        status = "PICKED"
    elif any(line.picked or line.backordered for line in lines):
        status = "PARTIAL"
    else:
        status = "SUBMITTED"
    order.status = status
    order.save(update_fields=["status"])


@transaction.atomic
def fulfill(
    actor,
    order_id,
    action,
    carrier="",
    tracking="",
    line_id=None,
    amount=None,
    scan="",
    key=None,
    note="",
    unit="BASE",
    requisition_id=None,
):
    require_warehouse(actor)
    order = Order.objects.select_for_update().get(pk=order_id)
    if once(
        actor,
        key,
        order=order_id,
        action=action,
        line=line_id,
        amount=amount,
        scan=scan,
        carrier=carrier,
        tracking=tracking,
        note=note,
        unit=unit,
        requisition=requisition_id,
    ):
        return order
    if order.status in ("SHIPPED", "CANCELLED"):
        raise WorkflowError("This order is closed.")
    list(
        Part.objects.select_for_update()
        .filter(pk__in=order.lines.values("part_id"))
        .order_by("pk")
    )
    requisition = None
    if action in {"pick", "pack", "ship", "unpick", "unpack"}:
        requisition = select_requisition(order, requisition_id)
    if action == "allocate":
        from .services import reserve_line

        allocations = []
        for line in order.lines.select_related("part").order_by("part_id"):
            allocations.extend(reserve_line(actor, line, strict=False))
        attach_allocations(order, allocations)
    elif action == "pick":
        lines = order.lines.filter(requisition_lines__requisition=requisition)
        if line_id:
            lines = lines.filter(pk=line_id)
        if not lines.exists():
            raise WorkflowError("Select a valid order line.")
        did = False
        for line in lines:
            from .units import convert

            row = release_line(requisition, line)
            want, entry = (
                convert(line, amount, unit)
                if line_id
                else (
                    row.reserved,
                    {
                        "base_quantity": str(row.reserved),
                        "base_unit": line.base_unit or "UNVERIFIED",
                    },
                )
            )
            if not want:
                continue
            if want > row.reserved:
                raise WorkflowError(
                    "Pick exceeds reserved quantity. Allocate stock first."
                )
            allocations = list(
                line.allocations.filter(state="RESERVED", requisition=requisition)
                .select_related("stock")
                .order_by("stock_id", "pk")
            )
            candidates = [
                a
                for a in allocations
                if a.stock.kind == "PICK"
                and not a.stock.held
                and not a.stock.count_required
            ]
            if sum((a.quantity for a in candidates), Decimal(0)) < want:
                raise WorkflowError(
                    "Replenish the allocated overstock to a counted pick location before confirming the pick."
                )
            remaining = want
            for a in candidates:
                take = min(a.quantity, remaining)
                if not take:
                    continue
                stock = Stock.objects.select_for_update().get(pk=a.stock_id)
                if (
                    stock.on_hand is None
                    or stock.on_hand < take
                    or stock.reserved < take
                ):
                    raise WorkflowError("Stock changed. Recheck this pick.")
                before = stock.on_hand
                stock.on_hand -= take
                stock.reserved -= take
                stock.save()
                if a.quantity == take:
                    a.state = "PICKED"
                    a.save(update_fields=["state"])
                else:
                    a.quantity -= take
                    a.save(update_fields=["quantity"])
                    Allocation.objects.create(
                        line=line,
                        stock=stock,
                        quantity=take,
                        state="PICKED",
                        release_id=a.release_id,
                        requisition=requisition,
                    )
                Event.objects.create(
                    actor=actor,
                    order=order,
                    stock=stock,
                    action="PICK",
                    quantity=-take,
                    before=before,
                    after=stock.on_hand,
                    entry={
                        **entry,
                        "bin_quantity": str(take),
                        "requisition": requisition.reference,
                    },
                )
                remaining -= take
            line.picked += want
            line.save(update_fields=["picked"])
            row.picked += want
            row.save(update_fields=["picked"])
            did = True
        if not did:
            raise WorkflowError("No reserved quantity remains to pick.")
    elif action == "pack":
        try:
            line = order.lines.select_related("part").get(pk=line_id)
        except (OrderLine.DoesNotExist, ValueError, TypeError):
            raise WorkflowError("Select an order line to verify.")
        if scan.strip() not in {line.part.sku, line.part.barcode}:
            raise WorkflowError("Scanned part does not match this order line.")
        from .units import convert

        qty, entry = convert(line, amount, unit)
        row = release_line(requisition, line)
        if qty > row.picked - row.packed:
            raise WorkflowError("Pack quantity exceeds picked, unpacked stock.")
        line.packed += qty
        line.save(update_fields=["packed"])
        row.packed += qty
        row.save(update_fields=["packed"])
        Event.objects.create(
            actor=actor,
            order=order,
            action="PACK_VERIFIED",
            quantity=qty,
            note=line.part.sku,
            entry={**entry, "requisition": requisition.reference},
        )
    elif action == "ship":
        if not carrier.strip() or not tracking.strip():
            raise WorkflowError("Enter carrier and tracking / PRO number.")
        if len(carrier.strip()) > 100 or len(tracking.strip()) > 150:
            raise WorkflowError("Carrier or tracking reference is too long.")
        rows = [
            row
            for row in requisition.lines.select_related("line")
            if row.packed > row.shipped
        ]
        if not rows:
            raise WorkflowError("Verify packing before shipping.")
        carry_remainder(order, requisition, actor)
        shipment = Shipment.objects.create(
            order=order,
            requisition=requisition,
            actor=actor,
            request_key=key or uuid.uuid4(),
            carrier=carrier.strip(),
            tracking=tracking.strip(),
        )
        for row in rows:
            line = row.line
            qty = row.packed - row.shipped
            ShipmentLine.objects.create(shipment=shipment, line=line, quantity=qty)
            line.shipped += qty
            line.save(update_fields=["shipped"])
            RequisitionLine.objects.filter(pk=row.pk).update(shipped=row.packed)
        order.carrier = shipment.carrier
        order.tracking = shipment.tracking
        order.shipped_at = shipment.created_at
        order.save()
        Event.objects.create(
            actor=actor,
            order=order,
            action="ORDER_SHIPPED",
            note=f"{requisition.reference}; {carrier}: {tracking}",
            entry={"requisition": requisition.reference},
        )
    elif action in ("unpack", "unpick"):
        if not note.strip():
            raise WorkflowError("Enter a correction reason.")
        try:
            line = order.lines.get(pk=line_id)
        except (OrderLine.DoesNotExist, ValueError, TypeError):
            raise WorkflowError("Select an order line.")
        from .units import convert

        qty, entry = convert(line, amount, unit)
        row = release_line(requisition, line)
        if action == "unpack":
            if qty > row.packed - row.shipped:
                raise WorkflowError("Only unshipped packed stock can be unpacked.")
            line.packed -= qty
            line.save(update_fields=["packed"])
            row.packed -= qty
            row.save(update_fields=["packed"])
        else:
            if qty > row.picked - row.packed:
                raise WorkflowError(
                    "Unpack first. Only unpacked, picked stock can be returned to its bin."
                )
            remaining = qty
            for a in line.allocations.filter(
                state="PICKED", requisition=requisition
            ).order_by("-pk"):
                take = min(a.quantity, remaining)
                if not take:
                    break
                stock = Stock.objects.select_for_update().get(pk=a.stock_id)
                if stock.held or stock.count_required or stock.on_hand is None:
                    raise WorkflowError(
                        "Original bin must be counted and released from hold before unpicking."
                    )
                before = stock.on_hand
                stock.on_hand += take
                stock.reserved += take
                stock.save()
                if a.quantity == take:
                    a.state = "RESERVED"
                    a.save(update_fields=["state"])
                else:
                    a.quantity -= take
                    a.save(update_fields=["quantity"])
                    Allocation.objects.create(
                        line=line,
                        stock=stock,
                        quantity=take,
                        release_id=a.release_id,
                        requisition=requisition,
                    )
                Event.objects.create(
                    actor=actor,
                    stock=stock,
                    order=order,
                    action="UNPICK",
                    quantity=take,
                    before=before,
                    after=stock.on_hand,
                    note=note,
                )
                remaining -= take
            if remaining:
                raise WorkflowError("Pick history is incomplete. Correction cancelled.")
            line.picked -= qty
            line.save(update_fields=["picked"])
            row.picked -= qty
            row.save(update_fields=["picked"])
        Event.objects.create(
            actor=actor,
            order=order,
            action=action.upper() + "_CONFIRMED",
            quantity=qty,
            note=note,
            entry={**entry, "requisition": requisition.reference},
        )
    elif action in ("cancel", "cancel_remaining"):
        if action == "cancel_remaining" and not note.strip():
            raise WorkflowError("Enter a cancellation reason.")
        if action == "cancel" and any(line.picked for line in order.lines.all()):
            raise WorkflowError(
                "Picked orders need physical stock reconciliation before cancellation."
            )
        for a in Allocation.objects.filter(
            line__order=order, state="RESERVED"
        ).select_related("stock"):
            stock = Stock.objects.select_for_update().get(pk=a.stock_id)
            stock.reserved -= a.quantity
            stock.save(update_fields=["reserved"])
            a.state = "RELEASED"
            a.save(update_fields=["state"])
            Event.objects.create(
                actor=actor,
                order=order,
                stock=stock,
                action="RELEASE",
                quantity=-a.quantity,
                before=stock.on_hand,
                after=stock.on_hand,
            )
        for line in order.lines.all():
            line.cancelled = line.quantity - line.picked
            line.save(update_fields=["cancelled"])
        for row in RequisitionLine.objects.filter(requisition__order=order):
            row.cancelled = row.quantity - row.picked
            row.save(update_fields=["cancelled"])
        Event.objects.create(
            actor=actor, order=order, action="CANCEL_UNPICKED", note=note
        )
    else:
        raise WorkflowError("Unknown warehouse action.")
    refresh_status(order)
    return order


@transaction.atomic
def receive_return(actor, line_id, stock_id, amount, note, key, unit="BASE"):
    require_warehouse(actor)
    if not note.strip():
        raise WorkflowError("Enter a return reason / reference.")
    initial = OrderLine.objects.get(pk=line_id)
    order = Order.objects.select_for_update().get(pk=initial.order_id)
    Part.objects.select_for_update().get(pk=initial.part_id)
    if once(
        actor,
        key,
        action="return",
        line=line_id,
        stock=stock_id,
        amount=amount,
        note=note,
        unit=unit,
    ):
        return
    line = OrderLine.objects.get(pk=line_id)
    stock = Stock.objects.select_for_update().get(pk=stock_id)
    from .units import convert

    if stock.archived:
        raise WorkflowError("Use an active quarantine bin.")
    qty, entry = convert(line, amount, unit)
    if stock.part_id != line.part_id:
        raise WorkflowError("Return location must contain the same part.")
    if not stock.held or stock.count_required or stock.on_hand is None:
        raise WorkflowError(
            "Return to a counted, held quarantine bin. Inspect before releasing the hold."
        )
    if qty > line.shipped - line.returned:
        raise WorkflowError("Return exceeds shipped quantity not already returned.")
    before = stock.on_hand
    stock.on_hand += qty
    stock.save(update_fields=["on_hand"])
    line.returned += qty
    line.save(update_fields=["returned"])
    ReturnReceipt.objects.create(
        request_key=key,
        line=line,
        stock=stock,
        quantity=qty,
        reason=note,
        actor=actor,
        entry=entry,
    )
    Event.objects.create(
        actor=actor,
        order=order,
        stock=stock,
        action="RETURN",
        quantity=qty,
        before=before,
        after=stock.on_hand,
        note=note,
        entry=entry,
    )


@transaction.atomic
def set_hold(actor, stock_id, held, note):
    require_warehouse(actor)
    if not note.strip():
        raise WorkflowError("Enter the hold or release reason.")
    part_id = Stock.objects.get(pk=stock_id).part_id
    from .backorders import lock_waiting_orders, release_waiting
    from .locking import coordinate_allocation

    coordinate_allocation([part_id])
    orders = lock_waiting_orders([part_id]) if not held else []
    Part.objects.select_for_update().get(pk=part_id)
    stock = Stock.objects.select_for_update().get(pk=stock_id)
    if held and stock.reserved:
        raise WorkflowError("Resolve reservations before placing this stock on hold.")
    if stock.archived:
        raise WorkflowError("This empty record is archived for history.")
    stock.held = held
    stock.hold_reason = note if held else ""
    stock.save(update_fields=["held", "hold_reason"])
    Event.objects.create(
        actor=actor, stock=stock, action="HOLD" if held else "RELEASE_HOLD", note=note
    )
    if not held:
        release_waiting(
            actor, orders, [part_id], reason="Quality hold released after inspection"
        )
