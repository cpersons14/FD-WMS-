"""Private, one-time FD inventory correction approved on November 13, 2026.

Only untouched ICC import balances are eligible. No counts, messages, orders,
or customer records are created. Approved location data is supplied privately through Render configuration.
"""

import os
import json
import hashlib
from collections import Counter
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import transaction
from warehouse.models import Part, Stock, Location, Event, OrderLine, Allocation
from warehouse.services import warehouse_user

IMPORT_ID = "5fddbc5d-3ec2-4bcb-a774-af4d2d73d5ea"
APPROVAL_ID = "FD-BINS-2026-11-13-CHRIS-COLIN"
ENV_NAME = "S3PL_FD_BIN_APPROVAL_JSON"
EXPECTED_PAYLOAD_SHA256 = (
    "6cf7c1e147864ec0a9642f2327baa2782a8a6bdebfe2d02dc33539533f27a4d5"
)


def validated_approval(raw):
    if not raw or len(raw) > 65536:
        raise ValueError("A valid private bin-approval configuration is required.")
    try:
        data = json.loads(raw)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid private bin-approval configuration.") from exc
    if hashlib.sha256(canonical).hexdigest() != EXPECTED_PAYLOAD_SHA256:
        raise ValueError(
            "Bin-approval configuration does not match the reviewed update; nothing changed."
        )
    return data


def apply_updates(actor_name="colin", apply=True):
    APPROVAL_DATA = validated_approval(os.environ.get(ENV_NAME, ""))
    SOURCE_NAME = APPROVAL_DATA["source"]
    actor = get_user_model().objects.get(username=actor_name, is_active=True)
    if not warehouse_user(actor):
        raise PermissionError("The audit user needs S3PL warehouse access.")
    records = APPROVAL_DATA["records"]
    report = []
    with transaction.atomic():
        # Existing stock-changing workflows also lock Part before Stock.
        parts = {
            p.sku: p
            for p in Part.objects.select_for_update()
            .filter(sku__in=[r["sku"] for r in records])
            .order_by("pk")
        }
        for record in records:
            sku = record["sku"]
            part = parts.get(sku)
            target = record["approved_bin"]
            disposed = record["disposed"]

            def add(status, detail):
                report.append({"sku": sku, "status": status, "detail": detail})

            if part is None:
                add("REVIEW", "Part not found; nothing changed.")
                continue
            if Event.objects.filter(
                stock__part=part, entry__approval_id=APPROVAL_ID
            ).exists():
                add(
                    "ALREADY APPLIED",
                    "This approval was recorded previously; later activity preserved.",
                )
                continue
            stocks = list(
                Stock.objects.select_for_update()
                .filter(part=part)
                .select_related("location")
                .order_by("pk")
            )
            originals = list(
                Event.objects.filter(
                    stock__part=part,
                    action="ICC_TEST_IMPORT",
                    entry__import_id=IMPORT_ID,
                ).order_by("pk")
            )
            if (
                not stocks
                or len(originals) != len(stocks)
                or {e.stock_id for e in originals} != {s.pk for s in stocks}
            ):
                add(
                    "REVIEW",
                    "Stock records differ from the original ICC import; nothing changed.",
                )
                continue
            by_stock = {e.stock_id: e for e in originals}
            expected_bins = {record["approved_bin"], record["rejected_bin"]}
            if any(
                {str(e.entry.get("source", {}).get(k, "")).strip() for k in ("L", "O")}
                != expected_bins
                for e in originals
            ):
                add(
                    "REVIEW",
                    "Source bins do not match the approved email; nothing changed.",
                )
                continue
            if (
                part.unit != "EA"
                or OrderLine.objects.filter(part=part).exists()
                or Allocation.objects.filter(stock__part=part).exists()
            ):
                add(
                    "REVIEW",
                    "Orders/allocations exist or base unit changed; nothing changed.",
                )
                continue
            if (
                Event.objects.filter(stock__part=part)
                .exclude(pk__in=[e.pk for e in originals])
                .exists()
            ):
                add("REVIEW", "Activity after the ICC import; nothing changed.")
                continue
            if any(
                s.archived
                or s.reserved
                or s.held
                or s.count_required
                or s.last_counted_at is not None
                or s.on_hand is None
                or s.on_hand != by_stock[s.pk].after
                for s in stocks
            ):
                add(
                    "REVIEW",
                    "Balances, holds, counts or archive status changed; nothing changed.",
                )
                continue
            if len({s.kind for s in stocks}) != 1:
                add(
                    "REVIEW",
                    "Pick/overstock classification differs between bins; nothing changed.",
                )
                continue
            total = sum((s.on_hand for s in stocks), Decimal(0))
            old = [
                {
                    "stock_id": s.pk,
                    "bin": s.location.code,
                    "on_hand": str(s.on_hand),
                    "kind": s.kind,
                }
                for s in stocks
            ]
            if not apply:
                add(
                    "READY",
                    f"Disposed: {total} EA to 0 EA; retain history."
                    if disposed
                    else f"{target}: retain total {total} EA.",
                )
                continue
            if disposed:
                for stock in stocks:
                    before = stock.on_hand
                    stock.on_hand = Decimal(0)
                    stock.archived = True
                    stock.save(update_fields=["on_hand", "archived"])
                    Event.objects.create(
                        actor=actor,
                        stock=stock,
                        action="DISPOSAL",
                        before=before,
                        after=Decimal(0),
                        quantity=-before,
                        entry={
                            "approval_id": APPROVAL_ID,
                            "source": SOURCE_NAME,
                            "source_sha256": APPROVAL_DATA["source_sha256"],
                            "prior_bins": old,
                            "disposal_confirmed": True,
                            "already_zero": before == 0,
                            "archived": True,
                        },
                        note="Colin confirmed Chris Sobota's disposal notice on 2026-11-13. "
                        "This records disposal, not a physical count. No disposal date was inferred.",
                    )
                add(
                    "DISPOSAL RECORDED",
                    f"{total} EA before; 0 EA after. Empty stock records archived; history retained.",
                )
                continue
            location, _ = Location.objects.get_or_create(code=target)
            keep = next((s for s in stocks if s.location_id == location.pk), stocks[0])
            for stock in stocks:
                before = stock.on_hand
                if stock.pk == keep.pk:
                    stock.location = location
                    stock.on_hand = total
                else:
                    stock.on_hand = Decimal(0)
                    stock.archived = True
                stock.save(update_fields=["location", "on_hand", "archived"])
                Event.objects.create(
                    actor=actor,
                    stock=stock,
                    action="ICC_LOCATION_CORRECTION",
                    before=before,
                    after=stock.on_hand,
                    quantity=stock.on_hand - before,
                    entry={
                        "approval_id": APPROVAL_ID,
                        "source": SOURCE_NAME,
                        "source_sha256": APPROVAL_DATA["source_sha256"],
                        "target": target,
                        "prior_bins": old,
                        "archived": stock.archived,
                    },
                    note="Green-highlighted bin approved by Colin from Chris Sobota's 2026-11-13 email. "
                    "Part total preserved. Not a receipt, transfer, or physical count.",
                )
            add("LOCATION UPDATED", f"{target}; total {total} EA preserved.")
    return report


def run_configured_approval(stdout):
    if not os.environ.get(ENV_NAME, "").strip():
        return []
    report = apply_updates()
    for row in report:
        stdout.write(f"{row['status']} | {row['sku']} | {row['detail']}")
    stdout.write(
        "FD BIN APPROVAL SUMMARY: "
        + str(dict(Counter(row["status"] for row in report)))
    )
    stdout.write(
        "FD bin approval complete. No customer emails or physical count dates changed."
    )
    if any(row["status"] == "REVIEW" for row in report):
        stdout.write("REVIEW records were preserved for individual reconciliation.")
    return report
