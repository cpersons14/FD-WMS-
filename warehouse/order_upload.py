import hashlib
import uuid
from django.db import transaction
from django.utils import timezone
from .models import StoreLocation, Part, OrderUpload, Event
from .services import (
    portal_user,
    quantity,
    WorkflowError,
    submit_order,
    validate_order_multiple,
)
from .tabular import read_table

HEADERS = ["Store Number", "Part Number", "Quantity EA", "Order Reference"]


@transaction.atomic
def preview_upload(actor, content, filename):
    if not portal_user(actor):
        raise PermissionError("Portal access required.")
    digest = hashlib.sha256(content).hexdigest()
    existing = (
        OrderUpload.objects.select_for_update()
        .filter(actor=actor, checksum=digest)
        .first()
    )
    if existing and existing.submitted_at:
        return existing
    rows = read_table(content, filename, HEADERS, max_rows=2000)
    stores = {
        s.store_number: s
        for s in StoreLocation.objects.filter(
            customer_code="FD",
            active=True,
            store_number__in=[r["Store Number"] for r in rows],
        )
    }
    parts = {
        p.sku: p for p in Part.objects.filter(sku__in=[r["Part Number"] for r in rows])
    }
    grouped = {}
    for n, row in enumerate(rows, 2):
        number = row["Store Number"]
        sku = row["Part Number"]
        reference = row["Order Reference"]
        if number not in stores:
            raise WorkflowError(
                f"Row {n}: store {number} is not in the active FD directory."
            )
        if sku not in parts or parts[sku].unit != "EA":
            raise WorkflowError(f"Row {n}: part {sku} must exist with EA confirmed.")
        qty = quantity(row["Quantity EA"])
        if qty != qty.to_integral_value():
            raise WorkflowError(f"Row {n}: use whole EA quantities.")
        try:
            validate_order_multiple(parts[sku], qty)
        except WorkflowError as exc:
            raise WorkflowError(f"Row {n}, store {number}: {exc}") from exc
        if len(reference) > 150:
            raise WorkflowError(f"Row {n}: order reference is too long.")
        key = (number, reference)
        group = grouped.setdefault(
            key,
            {
                "store_id": stores[number].pk,
                "details": stores[number].order_details(),
                "reference": reference,
                "items": {},
            },
        )
        group["items"][sku] = str(quantity(Decimal(group["items"].get(sku, "0")) + qty))
    if len(grouped) > 200:
        raise WorkflowError("Use at most 200 store/reference groups per file.")
    upload, _ = OrderUpload.objects.get_or_create(
        actor=actor,
        checksum=digest,
        defaults={"filename": filename[:255], "preview": list(grouped.values())},
    )
    upload = OrderUpload.objects.select_for_update().get(pk=upload.pk)
    if not upload.submitted_at:
        upload.preview = list(grouped.values())
        upload.save(update_fields=["preview"])
    return upload


from decimal import Decimal


@transaction.atomic
def submit_upload(actor, upload_id):
    if not portal_user(actor):
        raise PermissionError("Portal access required.")
    upload = OrderUpload.objects.select_for_update().get(pk=upload_id, actor=actor)
    if upload.submitted_at:
        return upload
    # Lock all items in one consistent order before submitting any of the store orders.
    skus = {sku for group in upload.preview for sku in group["items"]}
    from .locking import coordinate_allocation

    coordinate_allocation(
        Part.objects.filter(sku__in=skus).values_list("pk", flat=True)
    )
    list(Part.objects.select_for_update().filter(sku__in=skus).order_by("pk"))
    ids = []
    for i, group in enumerate(upload.preview):
        store = StoreLocation.objects.filter(
            pk=group["store_id"], customer_code="FD", active=True
        ).first()
        if not store or store.order_details() != group["details"]:
            raise WorkflowError(
                "A store address changed after preview. Return to Upload orders and upload the file again to refresh the preview before confirming."
            )
        details = {
            **group["details"],
            "store_location": store,
            "reference": group["reference"],
            "notes": "Excel / CSV order upload",
            "allow_backorder": True,
            "priority": "NORMAL",
        }
        order = submit_order(
            actor,
            details,
            [(sku, qty, "EA") for sku, qty in group["items"].items()],
            uuid.uuid5(upload.pk, str(i)),
        )
        ids.append(str(order.pk))
    upload.order_ids = ids
    upload.submitted_at = timezone.now()
    upload.save(update_fields=["order_ids", "submitted_at"])
    Event.objects.create(
        actor=actor,
        action="ORDER_FILE_SUBMITTED",
        entry={"upload": str(upload.pk), "sha256": upload.checksum, "orders": ids},
        note=upload.filename,
    )
    return upload
