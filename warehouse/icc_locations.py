"""Apply only Colin's approved bin rules to untouched original ICC test stock."""

from decimal import Decimal
from django.db import transaction
from .models import Part, Stock, Location, Allocation, Event
from .operations import require_warehouse

IMPORT_ID = "5fddbc5d-3ec2-4bcb-a774-af4d2d73d5ea"


def approved_bin(source):
    bins = list(
        dict.fromkeys(
            str(source.get(k, "")).strip()
            for k in ("L", "O")
            if str(source.get(k, "")).strip()
        )
    )
    real = [b for b in bins if b.upper() not in ("TBD", "MST BX")]
    if len(real) == 1:
        return real[0]
    if not real and len(bins) == 1:
        return bins[0]
    return None  # Do not split H20 H24, guess between real bins, or invent blank bins.


@transaction.atomic
def correct_locations(actor, apply=False):
    require_warehouse(actor)
    events = list(
        Event.objects.filter(action="ICC_TEST_IMPORT", entry__import_id=IMPORT_ID)
        .select_related("stock")
        .order_by("pk")
    )
    grouped = {}
    for event in events:
        grouped.setdefault(event.stock.part_id, []).append(event)
    parts = list(Part.objects.select_for_update().filter(pk__in=grouped).order_by("pk"))
    results = []
    for part in parts:
        originals = grouped[part.pk]
        target = approved_bin(originals[0].entry["source"])

        def record(status, reason):
            results.append(
                dict(sku=part.sku, status=status, reason=reason, target=target or "")
            )

        if not target:
            record("REVIEW", "Multiple real bins or blank source bin")
            continue
        stocks = list(
            Stock.objects.select_for_update()
            .filter(part=part)
            .select_related("location")
            .order_by("pk")
        )
        if Event.objects.filter(
            stock__part=part, action="ICC_LOCATION_CORRECTION"
        ).exists():
            record("UNCHANGED", "Already corrected")
            continue
        originals_by_stock = {e.stock_id: e for e in originals}
        if (
            set(originals_by_stock) != {s.pk for s in stocks}
            or Allocation.objects.filter(stock__part=part).exists()
            or Event.objects.filter(stock__part=part)
            .exclude(action="ICC_TEST_IMPORT")
            .exists()
        ):
            record(
                "REVIEW",
                "Activity or additional bin records since import; balances preserved",
            )
            continue
        if any(
            s.reserved
            or s.held
            or s.count_required
            or s.last_counted_at
            or s.archived
            or s.on_hand != originals_by_stock[s.pk].after
            for s in stocks
        ):
            record("REVIEW", "Balance/count/hold changed since import; preserved")
            continue
        keep = next((s for s in stocks if s.location.code == target), stocks[0])
        total = sum((s.on_hand for s in stocks), Decimal(0))
        if len(stocks) == 1 and keep.location.code == target:
            record("UNCHANGED", "Source bin already retained")
            continue
        record(
            "APPLY" if apply else "READY",
            f"Keep {total} EA in {target}; archive only superseded empty records",
        )
        if not apply:
            continue
        old = [
            dict(id=s.pk, location=s.location.code, on_hand=str(s.on_hand))
            for s in stocks
        ]
        location, _ = Location.objects.get_or_create(code=target)
        for stock in stocks:
            before = stock.on_hand
            if stock.pk == keep.pk:
                stock.location = location
                stock.on_hand = total
            else:
                stock.on_hand = 0
                stock.archived = True
            stock.save(update_fields=["location", "on_hand", "archived"])
            Event.objects.create(
                actor=actor,
                stock=stock,
                action="ICC_LOCATION_CORRECTION",
                before=before,
                after=stock.on_hand,
                entry={"prior_bins": old, "target": target, "archived": stock.archived},
                note="Approved source-bin rules only. Total quantity preserved; not a physical count.",
            )
    return results
