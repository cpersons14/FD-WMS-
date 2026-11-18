"""WMS-generated document/release identities and release-scoped quantities."""

from collections import defaultdict
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from .models import (
    Allocation,
    DocumentSequence,
    Event,
    Order,
    Requisition,
    RequisitionLine,
)
from .services import WorkflowError


def next_document_number():
    # Called inside order submission's transaction. The row lock protects simultaneous orders.
    DocumentSequence.objects.get_or_create(name="FD_DOCUMENT")
    sequence = DocumentSequence.objects.select_for_update().get(name="FD_DOCUMENT")
    sequence.value += 1
    sequence.save(update_fields=["value"])
    return f"FD{sequence.value:06d}"


def new_requisition(order):
    # Always take the parent lock; never derive a number from a row count.
    locked = Order.objects.select_for_update().get(pk=order.pk)
    number = locked.next_shipment_number
    req = Requisition.objects.create(
        order=locked,
        shipment_number=number,
        reference=f"{locked.document_number} {number:03d}",
    )
    locked.next_shipment_number += 1
    locked.save(update_fields=["next_shipment_number"])
    order.next_shipment_number = locked.next_shipment_number
    return req


def attach_allocations(order, allocations, requisition=None):
    if not allocations:
        return None
    if requisition is None:
        # An entirely backordered order already owns shipment 001. Its first stock uses it.
        requisition = order.requisitions.filter(
            lines__isnull=True, shipment__isnull=True
        ).first()
        if requisition is None:
            requisition = new_requisition(order)
    if requisition.order_id != order.pk or hasattr(requisition, "shipment"):
        raise WorkflowError("Allocations require an open requisition on this document.")
    totals = defaultdict(Decimal)
    for allocation in allocations:
        if allocation.line.order_id != order.pk or allocation.requisition_id:
            raise WorkflowError(
                "Allocation is already assigned or belongs to another document."
            )
        allocation.requisition = requisition
        allocation.save(update_fields=["requisition"])
        totals[allocation.line_id] += allocation.quantity
    for line_id, quantity in totals.items():
        row, created = RequisitionLine.objects.get_or_create(
            requisition=requisition, line_id=line_id, defaults={"quantity": quantity}
        )
        if not created:
            row.quantity += quantity
            row.save(update_fields=["quantity"])
    return requisition


def select_requisition(order, requisition_id=None):
    choices = order.requisitions.filter(shipment__isnull=True).prefetch_related("lines")
    if requisition_id:
        try:
            return choices.get(pk=requisition_id)
        except (Requisition.DoesNotExist, ValueError, ValidationError):
            raise WorkflowError(
                "Choose an open requisition belonging to this document."
            )
    active = [
        req
        for req in choices
        if any(row.quantity > row.cancelled for row in req.lines.all())
    ]
    if len(active) != 1:
        raise WorkflowError("Choose the requisition to pick, pack or ship.")
    return active[0]


def release_line(requisition, line):
    try:
        return requisition.lines.get(line_id=line.pk)
    except RequisitionLine.DoesNotExist:
        raise WorkflowError("This part is not on the selected requisition.")


def carry_remainder(order, requisition, actor):
    """A partial dispatch closes this reference and moves unshipped work to a new one.

    Caller holds the order lock. No stock or aggregate order quantities change.
    """
    rows = [
        row
        for row in requisition.lines.all()
        if row.quantity > row.packed + row.cancelled
    ]
    if not rows:
        return None
    next_req = new_requisition(order)
    moved = []
    for row in rows:
        remaining = row.quantity - row.packed - row.cancelled
        picked = row.picked - row.packed
        moved.append(
            {"line": row.line_id, "quantity": str(remaining), "picked": str(picked)}
        )
        RequisitionLine.objects.create(
            requisition=next_req,
            line_id=row.line_id,
            quantity=remaining,
            picked=picked,
        )
        Allocation.objects.filter(
            requisition=requisition,
            line_id=row.line_id,
            state="RESERVED",
        ).update(requisition=next_req)
        for allocation in Allocation.objects.filter(
            requisition=requisition,
            line_id=row.line_id,
            state="PICKED",
        ).order_by("-pk"):
            take = min(picked, allocation.quantity)
            if not take:
                break
            if take == allocation.quantity:
                allocation.requisition = next_req
                allocation.save(update_fields=["requisition"])
            else:
                allocation.quantity -= take
                allocation.save(update_fields=["quantity"])
                Allocation.objects.create(
                    requisition=next_req,
                    line_id=row.line_id,
                    stock_id=allocation.stock_id,
                    quantity=take,
                    state="PICKED",
                    release_id=allocation.release_id,
                )
            picked -= take
        if picked:
            raise WorkflowError("Pick history is incomplete. Shipment cancelled.")
        row.quantity -= remaining
        row.picked = row.packed
        if row.quantity:
            row.save(update_fields=["quantity", "picked"])
        else:
            row.delete()
    Event.objects.create(
        order=order,
        actor=actor,
        action="REQUISITION_SPLIT",
        note=f"Unshipped remainder moved from {requisition.reference} to {next_req.reference}.",
        entry={"from": requisition.reference, "to": next_req.reference, "lines": moved},
    )
    return next_req


@transaction.atomic
def match_reference(reference):
    """Exact canonical match shared by future UPS Ref1 and Freightview PO# feeds."""
    canonical = " ".join(str(reference).split()).upper()
    try:
        return Requisition.objects.select_related("order").get(reference=canonical)
    except Requisition.DoesNotExist:
        raise WorkflowError("No requisition matches this Document Shipment reference.")
