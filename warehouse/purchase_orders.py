from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from .models import (
    PurchaseOrder,
    PurchaseOrderLine,
    InboundReceipt,
    InboundReceiptLine,
    Part,
    Event,
    OperationKey,
)
from .operations import require_warehouse
from .services import WorkflowError, quantity
from .tabular import read_table
import hashlib
import uuid

HEADERS = ["PO Number", "Vendor", "Part Number", "Expected EA"]


@transaction.atomic
def import_purchase_orders(actor, content, filename):
    require_warehouse(actor)
    rows = read_table(content, filename, HEADERS)
    grouped = {}
    parts = {
        p.sku: p for p in Part.objects.filter(sku__in=[r["Part Number"] for r in rows])
    }
    for n, row in enumerate(rows, 2):
        number = row["PO Number"].upper()
        vendor = row["Vendor"]
        sku = row["Part Number"]
        qty = quantity(row["Expected EA"])
        if not number or len(number) > 100 or not vendor or len(vendor) > 150:
            raise WorkflowError(
                f"Row {n}: enter PO number and vendor within the field limits."
            )
        if sku not in parts or parts[sku].unit != "EA":
            raise WorkflowError(f"Row {n}: part {sku} must exist with EA confirmed.")
        if qty != qty.to_integral_value():
            raise WorkflowError(f"Row {n}: Expected EA must be a whole number.")
        group = grouped.setdefault(number, {"vendor": vendor, "items": {}})
        if group["vendor"] != vendor or sku in group["items"]:
            raise WorkflowError(f"Row {n}: duplicate PO/part or inconsistent vendor.")
        group["items"][sku] = qty
    # Serializes overlapping imports and avoids race-created duplicate POs.
    lock, _ = OperationKey.objects.get_or_create(
        key=uuid.uuid5(uuid.NAMESPACE_URL, "s3pl-fd-po-import-lock"),
        defaults={"actor": actor, "fingerprint": "PO import lock"},
    )
    OperationKey.objects.select_for_update().get(pk=lock.pk)
    digest = hashlib.sha256(content).hexdigest()
    key = uuid.uuid5(uuid.NAMESPACE_URL, "s3pl-fd-po-report:" + digest)
    if OperationKey.objects.filter(pk=key).exists():
        return {"unchanged": True, "orders": 0, "lines": 0}
    changes = []
    for number, group in sorted(grouped.items()):
        po, created = PurchaseOrder.objects.get_or_create(
            number=number, defaults={"vendor": group["vendor"], "created_by": actor}
        )
        if po.vendor != group["vendor"]:
            raise WorkflowError(
                f"PO {number}: vendor differs from the saved PO. Review it before importing."
            )
        for sku, expected in group["items"].items():
            line, new = PurchaseOrderLine.objects.get_or_create(
                purchase_order=po,
                part=parts[sku],
                defaults={"expected_quantity": expected},
            )
            if not new and line.expected_quantity != expected:
                raise WorkflowError(
                    f"PO {number}, {sku}: expected quantity differs from the saved PO. Use the PO detail correction form with a reason."
                )
            if new:
                changes.append({"po": number, "sku": sku, "expected": str(expected)})
    OperationKey.objects.create(key=key, actor=actor, fingerprint=digest)
    Event.objects.create(
        actor=actor,
        action="PO_IMPORT",
        entry={"sha256": digest, "new_lines": changes},
        note=filename[:255],
    )
    return {"unchanged": False, "orders": len(grouped), "lines": len(changes)}


def progress(po):
    accepted = dict(
        InboundReceiptLine.objects.filter(receipt__status="POSTED", po_number=po.number)
        .values("sku")
        .annotate(total=Sum("accepted_quantity"))
        .values_list("sku", "total")
    )
    rows = []
    for line in po.lines.select_related("part").order_by("part__sku"):
        got = accepted.get(line.part.sku, Decimal(0))
        expected = line.expected_quantity
        rows.append(
            {
                "line": line,
                "accepted": got,
                "remaining": max(Decimal(0), expected - got),
                "over": max(Decimal(0), got - expected),
            }
        )
    total = sum((r["line"].expected_quantity for r in rows), Decimal(0))
    filled = sum(
        (min(r["accepted"], r["line"].expected_quantity) for r in rows), Decimal(0)
    )
    return {
        "po": po,
        "rows": rows,
        "expected": total,
        "accepted": sum((r["accepted"] for r in rows), Decimal(0)),
        "percent": filled * 100 / total if total else 0,
        "complete": bool(rows) and all(r["remaining"] == 0 for r in rows),
        "unmatched": [
            (sku, q)
            for sku, q in accepted.items()
            if sku not in {r["line"].part.sku for r in rows}
        ],
    }


@transaction.atomic
def correct_expected(actor, line_id, amount, note):
    require_warehouse(actor)
    if not note.strip():
        raise WorkflowError("Enter a correction reason.")
    amount = quantity(amount)
    if amount != amount.to_integral_value():
        raise WorkflowError("Use a whole number of eaches.")
    line = (
        PurchaseOrderLine.objects.select_for_update()
        .select_related("purchase_order", "part")
        .get(pk=line_id)
    )
    before = line.expected_quantity
    line.expected_quantity = amount
    line.save(update_fields=["expected_quantity"])
    Event.objects.create(
        actor=actor,
        action="PO_EXPECTED_CORRECTION",
        before=before,
        after=amount,
        entry={"po": line.purchase_order.number, "sku": line.part.sku},
        note=note[:500],
    )


@transaction.atomic
def correct_accepted(actor, line_id, amount, note, po_number=None):
    require_warehouse(actor)
    if not note.strip():
        raise WorkflowError("Enter the inspection / correction reason.")
    amount = quantity(amount, allow_zero=True)
    line = (
        InboundReceiptLine.objects.select_for_update()
        .select_related("receipt", "stock")
        .get(pk=line_id)
    )
    if (
        line.receipt.status != "POSTED"
        or amount > line.quantity
        or amount != amount.to_integral_value()
    ):
        raise WorkflowError(
            "Accepted EA must be a whole number within the posted received quantity."
        )
    if amount > line.accepted_quantity and line.stock.held:
        raise WorkflowError(
            "Complete inspection and release the stock hold before accepting it against the PO."
        )
    before_po = line.po_number
    if po_number is not None:
        if len(po_number.strip()) > 100:
            raise WorkflowError("PO number must be at most 100 characters.")
        line.po_number = po_number.strip().upper()
    before = line.accepted_quantity
    line.accepted_quantity = amount
    line.save(update_fields=["accepted_quantity", "po_number"])
    Event.objects.create(
        actor=actor,
        receipt=line.receipt,
        stock=line.stock,
        action="PO_ACCEPTED_CORRECTION",
        before=before,
        after=amount,
        entry={
            "po": line.po_number,
            "prior_po": before_po,
            "sku": line.sku,
            "receipt_line": line.pk,
        },
        note=note[:500],
    )


@transaction.atomic
def correct_receipt_po(actor, receipt_id, number, note):
    require_warehouse(actor)
    number = number.strip().upper()
    if len(number) > 100 or not note.strip():
        raise WorkflowError("Enter a valid PO number and a reason.")
    receipt = InboundReceipt.objects.select_for_update().get(
        pk=receipt_id, status="POSTED"
    )
    before = receipt.po_number
    receipt.po_number = number
    receipt.save(update_fields=["po_number"])
    updated = list(receipt.lines.filter(po_number=before).values_list("pk", flat=True))
    receipt.lines.filter(pk__in=updated).update(po_number=number)
    Event.objects.create(
        actor=actor,
        receipt=receipt,
        action="RECEIPT_PO_CORRECTION",
        entry={"before": before, "after": number, "lines_updated": updated},
        note=note[:500],
    )
