"""Additive, quantity-free backstock references. No Stock/Location writes."""

import hashlib
import io
import json
import os
import re
import zipfile
from django.contrib.auth import get_user_model
from django.db import transaction
from .models import BackstockImport, BackstockReference, Event, Part
from .services import WorkflowError, warehouse_user

ENV_NAME = "S3PL_FD_BACKSTOCK_JSON"
EXPECTED_CONFIG_SHA256 = (
    "f056024cdd235136a3bda372ee6afb942db3176733fa8b139804c345bf7c0db1"
)


def _text(value, limit, label):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    result = str(value).strip()
    if len(result) > limit or any(ord(c) < 32 and c not in "\t\n" for c in result):
        raise WorkflowError(f"{label} is too long or contains unsupported characters.")
    return result


def read_locations(content, filename):
    if not filename.lower().endswith(".xlsx") or len(content) > 8 * 1024 * 1024:
        raise WorkflowError("Choose an XLSX workbook no larger than 8 MB.")
    from openpyxl import load_workbook

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 80 * 1024 * 1024:
                raise WorkflowError("The expanded workbook is too large.")
        book = load_workbook(
            io.BytesIO(content), read_only=True, data_only=False, keep_links=False
        )
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("This file could not be read as an XLSX workbook.") from exc
    try:
        if "FAMILY DOLLAR" not in book.sheetnames:
            raise WorkflowError("The workbook needs a FAMILY DOLLAR tab.")
        sheet = book["FAMILY DOLLAR"]
        if sheet.max_row > 10001:
            raise WorkflowError("Use at most 10,000 location rows.")
        if [
            str(sheet.cell(1, c).value or "").strip().casefold() for c in range(1, 4)
        ] != ["part", "description", "bin location"]:
            raise WorkflowError(
                "Columns A–C must be Part, Description, and Bin Location."
            )
        records = {}
        picks = 0
        # Deliberately never read column D or any quantity/formula columns.
        for cells in sheet.iter_rows(min_row=2, max_col=3):
            a, b, c = cells
            if not any(x.value is not None for x in cells):
                continue
            if c.font.bold:
                picks += 1
                continue
            if any(x.data_type == "f" for x in cells):
                raise WorkflowError(
                    f"Row {a.row}: part, description and bin must be plain values."
                )
            sku = _text(a.value, 100, f"Row {a.row} part")
            location = _text(c.value, 100, f"Row {a.row} bin")
            if not sku or not location:
                raise WorkflowError(f"Row {a.row}: both part and bin are required.")
            key = (sku, location)
            rec = records.setdefault(
                key,
                dict(
                    sku=sku,
                    description=_text(b.value, 500, "Description"),
                    location=location,
                    source_rows=[],
                ),
            )
            rec["source_rows"].append(a.row)
        if not records:
            raise WorkflowError("No non-bold backstock locations were found.")
        return dict(
            source_sha256=hashlib.sha256(content).hexdigest(),
            source_name=_text(filename, 255, "Filename"),
            source_sheet=sheet.title,
            ignored_pick_rows=picks,
            references=list(records.values()),
        )
    finally:
        book.close()


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) - {
        "source_sha256",
        "source_name",
        "source_sheet",
        "ignored_pick_rows",
        "references",
        "order_multiples",
    }:
        raise WorkflowError("Invalid backstock reference data.")
    if not re.fullmatch(r"[a-f0-9]{64}", str(payload.get("source_sha256", ""))):
        raise WorkflowError("Source checksum is missing.")
    if payload.get("source_sheet") != "FAMILY DOLLAR":
        raise WorkflowError("Only the FAMILY DOLLAR tab is supported.")
    if not payload.get("source_name") or len(payload["source_name"]) > 255:
        raise WorkflowError("Source filename is missing or too long.")
    rows = payload.get("references")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise WorkflowError("No usable backstock references.")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "sku",
            "description",
            "location",
            "source_rows",
        }:
            raise WorkflowError(
                "A location reference must not contain quantities or other fields."
            )
        for name, limit in [("sku", 100), ("description", 500), ("location", 100)]:
            if (
                not isinstance(row[name], str)
                or _text(row[name], limit, name) != row[name]
                or (name != "description" and not row[name])
            ):
                raise WorkflowError("Invalid location reference.")
        if (
            not isinstance(row["source_rows"], list)
            or not row["source_rows"]
            or any(type(n) is not int or n < 2 for n in row["source_rows"])
        ):
            raise WorkflowError("Invalid source row.")
    if (
        type(payload.get("ignored_pick_rows")) is not int
        or payload["ignored_pick_rows"] < 0
    ):
        raise WorkflowError("Invalid pick-row count.")
    rules = payload.get("order_multiples", [])
    if not isinstance(rules, list):
        raise WorkflowError("Invalid order increments.")
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or set(rule) != {"sku", "multiple"}
            or not isinstance(rule["sku"], str)
            or not rule["sku"]
            or type(rule["multiple"]) is not int
            or not 1 <= rule["multiple"] <= 1000000
        ):
            raise WorkflowError("Invalid order increment.")


@transaction.atomic
def import_locations(actor, payload):
    if not warehouse_user(actor):
        raise PermissionError("Warehouse access required.")
    validate_payload(payload)
    # One import record makes repeat deployment/upload a no-op, including after manual edits.
    batch, created = BackstockImport.objects.get_or_create(
        checksum=payload["source_sha256"],
        defaults=dict(
            filename=payload["source_name"],
            sheet=payload["source_sheet"],
            created_by=actor,
        ),
    )
    batch = BackstockImport.objects.select_for_update().get(pk=batch.pk)
    if not created:
        return batch, False
    rules = payload.get("order_multiples", [])
    parts = {
        p.sku: p
        for p in Part.objects.select_for_update()
        .filter(sku__in=[r["sku"] for r in rules])
        .order_by("pk")
    }
    if len(parts) != len({r["sku"] for r in rules}):
        raise WorkflowError(
            "An ordering-rule part is missing; the entire import was left unapplied."
        )
    for row in payload["references"]:
        ref, new = BackstockReference.objects.get_or_create(
            sku=row["sku"],
            location=row["location"],
            defaults=dict(
                description=row["description"],
                source_rows=row["source_rows"],
                source_import=batch,
                updated_by=actor,
            ),
        )
        batch.added_count += int(new)
        batch.existing_count += int(not new)
        if new:
            Event.objects.create(
                actor=actor,
                action="BACKSTOCK_REFERENCE_ADDED",
                entry={
                    "reference_id": ref.pk,
                    "sku": ref.sku,
                    "location": ref.location,
                    "source": batch.filename,
                    "source_rows": ref.source_rows,
                    "source_sha256": batch.pk,
                },
                note=f"{ref.sku} / {ref.location}: backstock location reference only. No quantity imported.",
            )
    for rule in rules:
        part = parts[rule["sku"]]
        before = part.order_multiple
        if before != rule["multiple"]:
            part.order_multiple = rule["multiple"]
            part.save(update_fields=["order_multiple"])
            Event.objects.create(
                actor=actor,
                action="ORDER_INCREMENT_CHANGED",
                entry={
                    "sku": part.sku,
                    "before_multiple": before,
                    "after_multiple": part.order_multiple,
                    "source_sha256": batch.pk,
                },
                note=f"{part.sku}: new orders must use multiples of {part.order_multiple} EA. Existing balances and orders unchanged.",
            )
    batch.row_count = sum(len(r["source_rows"]) for r in payload["references"])
    batch.ignored_pick_rows = payload["ignored_pick_rows"]
    batch.save(
        update_fields=[
            "row_count",
            "added_count",
            "existing_count",
            "ignored_pick_rows",
        ]
    )
    Event.objects.create(
        actor=actor,
        action="BACKSTOCK_IMPORT",
        entry={
            "source_sha256": batch.pk,
            "added": batch.added_count,
            "existing": batch.existing_count,
        },
        note=f"{batch.filename} / {batch.sheet}: {batch.added_count} references added; {batch.existing_count} existing references preserved. Quantities ignored; pick locations unchanged.",
    )
    return batch, True


def run_configured_backstock(stdout):
    raw = os.environ.get(ENV_NAME, "").strip()
    if not raw:
        return
    try:
        payload = json.loads(raw)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Invalid private backstock configuration.") from exc
    if digest != EXPECTED_CONFIG_SHA256:
        raise WorkflowError(
            "Private backstock configuration does not match the reviewed file."
        )
    actor = get_user_model().objects.get(username="colin", is_active=True)
    batch, created = import_locations(actor, payload)
    stdout.write(
        f"FD BACKSTOCK: {'APPLIED' if created else 'ALREADY APPLIED'}; {batch.added_count} references added originally, {batch.existing_count} existing references preserved. No stock quantities changed."
    )
