"""Coordinate new orders with the receipt/hold-release waiting-order snapshot."""

import hashlib

from django.db import connection


def coordinate_allocation(part_ids):
    """Take transaction-scoped gates before any order/part stock locks.

    Row locks cannot protect an order that has not been inserted yet. These
    per-part gates make an arriving order either visible to a receipt's waiting
    snapshot, or wait until the receipt commits and then see its new stock.
    Multi-store uploads acquire every gate before calling submit_order.
    Sorted lock keys prevent cross-part acquisition cycles. Hash collisions only
    serialize unrelated parts; they cannot weaken stock protection.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Allocation coordination requires a transaction.")
    if connection.vendor == "sqlite":
        return  # Development convenience only; release tests require PostgreSQL.
    if connection.vendor != "postgresql":
        raise RuntimeError("Unsupported inventory database.")
    keys = sorted(
        {
            int.from_bytes(
                hashlib.sha256(f"s3pl-fd-allocation:{pk}".encode()).digest()[:8],
                "big",
                signed=True,
            )
            for pk in part_ids
        }
    )
    with connection.cursor() as cursor:
        for key in keys:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
