"""Receipt-triggered releases. Caller locks orders BEFORE parts, in the receipt transaction."""

from django.db import transaction
from .models import Order, PickRelease, Event
from .services import reserve_line, WorkflowError
from .operations import require_warehouse, refresh_status


def lock_waiting_orders(part_ids):
    # A single total lock order avoids deadlocks between overlapping multi-part receipts.
    ids = (
        Order.objects.filter(lines__part_id__in=part_ids)
        .exclude(status__in=["SHIPPED", "CANCELLED"])
        .values("pk")
    )
    return list(Order.objects.select_for_update().filter(pk__in=ids).order_by("pk"))


def release_waiting(actor, orders, part_ids, receipt=None, reason="Stock released"):
    releases = []
    for order in sorted(
        orders, key=lambda o: (o.priority != "URGENT", o.created_at, str(o.pk))
    ):
        if order.status in ("SHIPPED", "CANCELLED"):
            continue
        allocations = []
        for line in (
            order.lines.filter(part_id__in=part_ids)
            .select_related("part")
            .order_by("part_id")
        ):
            allocations.extend(reserve_line(actor, line) or [])
        if not allocations:
            continue
        from .requisitions import attach_allocations

        requisition = attach_allocations(order, allocations)
        snapshot = [
            dict(
                sku=a.line.part.sku,
                location=a.stock.location.code,
                quantity=str(a.quantity),
            )
            for a in allocations
        ]
        release = PickRelease.objects.create(
            order=order,
            receipt=receipt,
            actor=actor,
            reason=reason,
            snapshot=snapshot,
            requisition=requisition,
        )
        for allocation in allocations:
            allocation.release = release
            allocation.save(update_fields=["release"])
        refresh_status(order)
        Event.objects.create(
            actor=actor,
            order=order,
            receipt=receipt,
            action="BACKORDER_RELEASE",
            quantity=sum(a.quantity for a in allocations),
            entry={"release": str(release.pk), "items": snapshot},
            note=release.number,
        )
        releases.append(release)
    return releases


@transaction.atomic
def change_priority(actor, order_id, priority, note):
    require_warehouse(actor)
    if priority not in ("NORMAL", "URGENT") or not note.strip():
        raise WorkflowError("Choose a priority and enter a reason.")
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status in ("SHIPPED", "CANCELLED"):
        raise WorkflowError("This order is closed.")
    before = order.priority
    order.priority = priority
    order.save(update_fields=["priority"])
    Event.objects.create(
        actor=actor,
        order=order,
        action="ORDER_PRIORITY",
        entry={"before": before, "after": priority},
        note=note.strip()[:500],
    )
