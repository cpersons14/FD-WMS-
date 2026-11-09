# What the S3PL Family Dollar WMS includes

Deployment copy prepared November 9, 2026. These functions are built in the pilot source package. The WMS has not been deployed or approved to replace Lawson in live operations.

| Area | Functionality |
| --- | --- |
| Standalone customer portal | Separate FD Corporate and S3PL Warehouse logins; named accounts; no public signup. The application can run separately from s3pl.com. |
| Customer inventory view | Search by part, description, location or recorded barcode. View on hand, reserved and available quantities. The inventory page refreshes every 30 seconds while open. |
| Location control | Pick and overstock bins; new part/bin setup; uncounted stock is distinct from zero and excluded from availability. |
| Office receiving | New Receipt; supplier/date/reference; multiple item and putaway-location rows keyed from the floor sheet; exception notes; Save Draft and Needs clarification; posted receipt history. Floor staff keep the paper process. |
| Receipt documents | Drag/drop or choose scanned BOLs, packing lists and photos. Preview, remove and select customer attachments before posting. Private PDF/JPG/PNG storage with size limits; retrieval by authorized warehouse staff. |
| Customer receipt email | One grouped itemized email per posted delivery, selected attachments, saved FD Receiving group, send history and separate email status. Pending send and reviewed retry never add inventory again. Sender, recipient addresses and enablement still require setup. |
| Case packs | Verified eaches per case and optional cases per standard pallet; each/case/pallet entry; loose eaches; calculated each totals and case equivalents. Orders/receipts retain original pack factors. No automatic rescaling of original inventory. |
| Corporate orders | FD can submit store orders, addresses, references and notes; urgent priority; requested ship date; optional backorders. Available stock is reserved. |
| Warehouse work | Replenishment suggestions, stock transfers with reservations, due cycle counts, quality holds and backorder queues. Staff confirm physical movement. |
| Picking and packing | Pick-location allocations, full or partial pick confirmation, typed part number or barcode verification, quantity checks and staged balances by order line. |
| Shipping | Partial shipments; per-shipment carrier, tracking/PRO and quantities; shipment history; printable pick/packing records. Carrier booking and labels are separate future integrations. |
| Exceptions and returns | Unpack/unpick corrections with reasons; cancellation of unpicked orders or remaining unpicked balances; bounded returns to held quarantine bins and inspection release. |
| Low-stock notification | Per-item thresholds, target stock and pick minimums; portal alerts and opt-in daily emails for active FD Corporate users. Email sender and scheduled digest need setup. |
| Counts and audit | Physical count entry, count-required flags, count dates/intervals, actor/time/reason ledger, before/after balances and conversion details. |
| Reports and data | Inventory CSV export, order/shipment history, receipt records, printable pick/packing records, opening inventory import capability. Real inventory is excluded from this deployment copy. |
| Access and transaction checks | Warehouse actions restricted by role, CSRF protection, login lockout, quantity constraints, transaction locks and duplicate-submission guards. |

This deployment copy starts with an empty database. Verified opening stock will be loaded later; no real inventory data is included.

Still separate work: live hosting, approved accounts/email addresses, initial counts and unit verification, warehouse acceptance, PostgreSQL concurrency/load testing, backup restore and security review, Lawson cutover, 4×6 pallet labels, scanner/tablet deployment, WorldShip/Freightview/carrier links, automatic shipment emails, multi-customer separation and billing. Lot tracking is not required for the agreed FD workflow. See README.md and RECEIVING_WORKFLOW.md for boundaries and setup.
