"""Seed an empty disposable local database for the restore-tool rehearsal."""

import base64
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"

import django

django.setup()  # test_settings validates the separate local database before any query.

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from warehouse.models import Location, Part, ReceiptGroup, Stock
from warehouse.receiving import post_receipt, read_uploads, save_receipt
from warehouse.services import submit_order, transition_order


@transaction.atomic
def main():
    if Part.objects.exists() or get_user_model().objects.exists():
        raise SystemExit("Rehearsal seed requires an empty disposable database.")
    actor = get_user_model().objects.create_superuser("synthetic-restore-operator")
    actor.set_unusable_password()
    actor.save(update_fields=["password"])
    part = Part.objects.create(
        sku="SYNTHETIC-RESTORE", description="Synthetic recovery sample", unit="EA"
    )
    stock = Stock.objects.create(
        part=part,
        location=Location.objects.create(code="TEST-PICK"),
        kind="PICK",
        on_hand=10,
    )
    details = dict(
        store="TEST-STORE",
        recipient="Synthetic only",
        address="1 Test Way",
        city="Test",
        state="TN",
        postal_code="00000",
        reference="RESTORE-TEST",
        notes="Synthetic",
        allow_backorder=True,
    )
    order = submit_order(actor, details, [(part.sku, 12, "EA")], uuid.uuid4())
    transition_order(actor, order.pk, "pick", key=uuid.uuid4())
    line = order.lines.get()
    transition_order(
        actor,
        order.pk,
        "pack",
        line_id=line.pk,
        amount=10,
        unit="EA",
        scan=part.sku,
        key=uuid.uuid4(),
    )
    transition_order(
        actor, order.pk, "ship", carrier="TEST", tracking="SYNTHETIC", key=uuid.uuid4()
    )
    group = ReceiptGroup.objects.create(
        name="Synthetic recipients", addresses="test@example.com"
    )
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBfsAAAAASUVORK5CYII="
    )
    receipt = save_receipt(
        actor,
        uuid.uuid4(),
        None,
        0,
        dict(
            supplier="Synthetic",
            received_date=timezone.localdate(),
            reference="TEST-BOL",
            po_number="TEST-PO",
            group=group,
            notes="Synthetic recovery exercise",
        ),
        [
            dict(
                sku=part.sku,
                location=stock.location.code,
                quantity="5",
                unit="EA",
                loose_eaches="0",
                case_pack="",
                pallet_cases="",
                damaged=False,
            )
        ],
        uploads=read_uploads(
            [SimpleUploadedFile("synthetic.png", image, content_type="image/png")]
        ),
        new_email_indices=[0],
    )
    post_receipt(actor, receipt.pk, receipt.revision)
    print(
        "Synthetic order, partial shipment, receipt, attachment, backorder release and audit rows created. No email sent."
    )


if __name__ == "__main__":
    main()
