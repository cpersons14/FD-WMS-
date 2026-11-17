"""Family Dollar ship-to directory. Warehouse bins are a separate model."""

import hashlib
import io
import re
import uuid
from pathlib import Path
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook

from .models import Event, OperationKey, StoreLocation
from .services import warehouse_user

HEADERS = [
    "Store Number",
    "Shipping Address",
    "Shipping City",
    "Shipping State/Province",
    "Shipping Zip/Postal Code",
    "Shipping County",
    "Location Type",
]
FIELDS = [
    "store_number",
    "address",
    "city",
    "state",
    "postal_code",
    "county",
    "location_type",
]
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20000


def read_report(content):
    if len(content) > MAX_BYTES:
        raise ValidationError("The location report must be 10 MB or smaller.")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 80 * 1024 * 1024:
                raise ValidationError("The expanded location report is too large.")
        workbook = load_workbook(
            io.BytesIO(content), read_only=True, data_only=False, keep_links=False
        )
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Upload a valid Excel .xlsx location report.") from exc
    try:
        matches = []
        for sheet in workbook:
            for cells in sheet.iter_rows(min_row=1, max_row=10, max_col=30):
                labels = [str(c.value or "").strip() for c in cells]
                if all(h in labels for h in HEADERS):
                    matches.append(
                        (sheet, cells[0].row, [labels.index(h) for h in HEADERS])
                    )
                    break
        if len(matches) != 1:
            raise ValidationError(
                "Expected one sheet with the seven FD Active Location Report column headers."
            )
        sheet, header, columns = matches[0]
        rows = []
        seen = set()
        for cells in sheet.iter_rows(
            min_row=header + 1, max_row=header + MAX_ROWS + 1, max_col=max(columns) + 1
        ):
            picked = [cells[i] for i in columns]
            if not any(c.value not in (None, "") for c in picked):
                continue
            if len(rows) >= MAX_ROWS:
                raise ValidationError("The report exceeds 20,000 locations.")
            if any(c.data_type in ("f", "e") for c in picked):
                raise ValidationError(
                    f"Row {cells[0].row}: use values, not formulas or Excel errors."
                )
            values = [
                str(c.value).strip() if c.value is not None else "" for c in picked
            ]
            # Numeric ZIPs in Excel can lose leading zeroes. FD addresses use US ZIPs.
            if isinstance(picked[4].value, int):
                values[4] = str(picked[4].value).zfill(5)
            data = dict(zip(FIELDS, values))
            data["state"] = data["state"].upper()
            if not data["store_number"] or data["store_number"] in seen:
                raise ValidationError(
                    f"Row {cells[0].row}: missing or duplicate store number {data['store_number']}."
                )
            if not re.fullmatch(r"\d{5}(?:-\d{4})?", data["postal_code"]):
                raise ValidationError(
                    f"Row {cells[0].row}: ZIP must contain five digits, optionally followed by -XXXX."
                )
            if not re.fullmatch(r"[A-Z]{2}", data["state"]):
                raise ValidationError(f"Row {cells[0].row}: state must be two letters.")
            try:
                StoreLocation(**data).full_clean(
                    validate_unique=False, validate_constraints=False
                )
            except ValidationError as exc:
                raise ValidationError(f"Row {cells[0].row}: {exc}") from exc
            seen.add(data["store_number"])
            rows.append(data)
        if not rows:
            raise ValidationError("The location report contains no stores.")
        return rows
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            "The report could not be read. Save it as an Excel .xlsx file and try again."
        ) from exc
    finally:
        workbook.close()


def import_report(actor, content, filename):
    if not warehouse_user(actor):
        raise PermissionError("Warehouse access required.")
    rows = read_report(
        content
    )  # Validate everything before changing any directory records.
    checksum = hashlib.sha256(content).hexdigest()
    key = uuid.uuid5(uuid.NAMESPACE_URL, "s3pl:fd-store-directory:" + checksum)
    with transaction.atomic():
        # Serialize directory imports, including two different reports uploaded together.
        lock_key = uuid.uuid5(uuid.NAMESPACE_URL, "s3pl:fd-store-directory-lock")
        OperationKey.objects.get_or_create(
            key=lock_key,
            defaults={"actor": actor, "fingerprint": "FD_STORE_DIRECTORY_LOCK"},
        )
        OperationKey.objects.select_for_update().get(key=lock_key)
        marker, new = OperationKey.objects.get_or_create(
            key=key,
            defaults={"actor": actor, "fingerprint": "FD_STORE_DIRECTORY:" + checksum},
        )
        if not new:
            return {
                "total": len(rows),
                "created": 0,
                "updated": 0,
                "unchanged": len(rows),
                "repeated": True,
            }
        existing = {
            s.store_number: s
            for s in StoreLocation.objects.select_for_update().filter(
                customer_code="FD"
            )
        }
        created = []
        updated = []
        changes = []
        now = timezone.now()
        source = Path(filename).name[:255]
        for data in rows:
            store = existing.get(data["store_number"])
            if store is None:
                created.append(StoreLocation(**data, source_report=source))
                changes.append(
                    {"store": data["store_number"], "before": None, "after": data}
                )
            elif any(getattr(store, f) != data[f] for f in FIELDS) or not store.active:
                changes.append(
                    {
                        "store": data["store_number"],
                        "before": {f: getattr(store, f) for f in FIELDS}
                        | {"active": store.active},
                        "after": data | {"active": True},
                    }
                )
                for field, value in data.items():
                    setattr(store, field, value)
                store.active = True
                store.source_report = source
                store.updated_at = now
                updated.append(store)
        StoreLocation.objects.bulk_create(created, batch_size=500)
        StoreLocation.objects.bulk_update(
            updated, FIELDS + ["active", "source_report", "updated_at"], batch_size=250
        )
        counts = {
            "total": len(rows),
            "created": len(created),
            "updated": len(updated),
            "unchanged": len(rows) - len(created) - len(updated),
            "repeated": False,
        }
        Event.objects.create(
            actor=actor,
            action="FD_STORE_DIRECTORY_IMPORT",
            entry={
                "sha256": checksum,
                "source": source,
                "counts": counts,
                "changes": changes,
            },
            note=f"FD ship-to directory imported: {len(rows)} stores. Existing orders retain their delivery addresses.",
        )
        return counts
