# WMS-generated Document and Shipment numbers

The WMS assigns every new order a Document number, such as `FD000001`.
It creates Shipment `001` when the order is submitted, including entirely
backordered orders. The combined requisition reference is `FD000001 001`.
The same behavior applies to office orders, customer orders and Excel uploads.
The optional Customer reference remains separate; customers do not supply WMS numbers.

## Office workflow

1. Open the order and select a requisition in its Requisitions table.
2. Print that requisition's picklist. It contains its own quantities and locations.
3. Confirm picking and packing on the selected requisition.
4. Enter the complete `Document Shipment` reference, including the space, into
   **UPS Ref1** on either WorldShip computer or **Freightview PO#**.
5. Confirm dispatch with its single UPS tracking number or LTL PRO.

When receipts release more stock, the Document stays the same and the WMS assigns
the next Shipment number. For an entirely backordered order, its first allocation
uses the already reserved `001`; subsequent releases get `002`, `003`, etc.
Different open requisitions can be picked and shipped independently.

Partial dispatches are supported: only verified packed quantities ship. The WMS
moves unshipped reserved stock and picked-but-unpacked stock to a new Shipment
number in the same transaction. The original reference retains its tracking.
The warehouse queue and printable picklist identify the remaining work by its new
reference. Receipt release pages remain audit records and link to current requisitions.

Cancelling the unpicked remainder still applies to the **entire document** and is
labelled accordingly. Shipped quantities require the existing returns workflow.

## Tracking integration boundary

This change establishes the identifiers and independent fulfillment records.
It does **not** connect the two WorldShip stations or enable Freightview/UPS API
updates. Tracking still uses the existing manual dispatch form.

`match_reference()` supports exact matching of the combined reference, allowing
case and whitespace normalization. It does not guess using document alone,
store, customer reference, or dates. Future imports must authenticate their source,
deduplicate incoming events and queue unmatched/conflicting references for review.
Carrier status updates must not allocate stock or confirm physical picking/shipping.

## Data and transaction rules

- `Order.document_number` is unique. `DocumentSequence` uses a locked counter row.
- Shipment numbers use the locked order's monotonically increasing counter.
  Committed and cancelled numbers are not recycled. Failed transactions roll back.
- `Requisition` is unique by Document/Shipment and by combined reference.
- `RequisitionLine` tracks released, picked, packed, shipped and cancelled quantities.
  Database constraints enforce the same quantity bounds as aggregate OrderLines.
- `Shipment.requisition` is one-to-one: a requisition has at most one dispatch record.
- Allocations retain their requisition through pick splits, unpick, transfers,
  receipt release and partial dispatch. Aggregate inventory remains unchanged when
  remaining work moves between requisitions.
- Picking/packing/shipping takes the existing Order then Part locks. The existing
  allocation coordination remains unchanged. New-order numbering takes its counter
  lock after the existing sorted Part locks; it does not acquire more Parts afterward.
- Browser mutation keys include the selected requisition. Identical retries do not
  create a second number, movement or shipment. With multiple open requisitions,
  service callers must select one explicitly.
- Numbering models are read-only in Django admin.

## Existing pilot data and deployment

Migrations `0016`–`0018` separate schema creation, data conversion and required-field
constraints. Existing orders receive sequential Document numbers in creation order.
Existing dispatches receive separate Shipment numbers in dispatch order. Open work
receives a separate requisition. Quantities, stock balances, tracking, customer
references, shipment records, original UUIDs and URLs are preserved. The former
`FD-XXXXXXXX` identifier remains searchable.

Historical allocations did not identify individual shipments. The migration links
only currently reserved stock and the unshipped picked remainder; it does not invent
historic shipment-to-bin links. Existing receipt release snapshots are preserved.
The migration stops for inconsistent shipped totals, excess reservations or incomplete
open pick history, so those records can be reconciled before retrying.

These new internal references do **not** rewrite historic UPS/Freightview records.
Previously booked carrier shipments require an explicit reviewed crosswalk before
automatic matching is enabled.

Follow `docs/RELEASE.md`: independent review, PostgreSQL CI, backup and a restored-data
migration rehearsal before production. **Pause order/warehouse writes for this upgrade.**
The old application cannot create the new requisition records, so this is not a
rolling-compatible database change. Stop old workers before reopening writes on the
new version. Do not roll back migrations on live data: that would discard the new
identities. Prefer a forward fix; a backup restore requires reconciling all later work.

Acceptance examples: place an order with a backorder; print `001`; ship it; receive
the remainder; verify `002` and its separate tracking. Also test a partial dispatch,
two concurrently open releases, an upload retry and an old pilot order after migration.
