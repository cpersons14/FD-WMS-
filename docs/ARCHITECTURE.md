# WMS architecture and change rules

This application serves Family Dollar only. It is not a multi-customer platform.
The source of truth is ordinary source files in Git, not generated build installers.

| Area | Responsibility |
| --- | --- |
| `warehouse/models.py` | Database records, quantity constraints, relationships |
| `services.py`, `operations.py` | Ordering, reservations, counts, transfers, fulfillment, returns |
| `receiving.py`, `backorders.py`, `purchase_orders.py` | Receipt posting, automatic releases, accepted PO quantities |
| `units.py` | Unit conversion and preserved pack snapshots |
| `order_upload.py`, `store_directory.py`, `backstock.py` | File validation and atomic imports |
| `*_views.py`, `views.py`, `forms.py` | Authentication, input validation and page responses |
| `templates/`, `static/` | Display and browser assistance; never the only enforcement of a rule |
| `warehouse/test*.py` | Workflow, migration, permission and concurrency regression tests |
| `config/` | Application configuration, URLs and isolated test settings |

## Invariants

- Physical storage balance, reservation, staged stock and shipped stock are different quantities.
- A reservation never increases physical stock. Reserved quantities cannot exceed counted stock.
- Unknown counts are different from zero and cannot be allocated.
- Held or archived stock is not orderable. Picked stock cannot be allocated a second time.
- Pick/pack/ship/return quantities must remain within their preceding workflow bounds.
- Posting a delivery and creating its email record are atomic. SMTP delivery happens separately.
- Retrying an uncertain email must never repost a delivery or silently send duplicates.
- Orders use EA; conversions and order increments are validated on the server.
- Backstock references have no quantity and never participate in allocation.
- Existing receipt/order pack snapshots survive later item-master changes.
- Every stock-changing workflow needs permission enforcement, a transaction, stable lock ordering,
  duplicate-action protection where applicable, and an audit event.

## Maintenance rules

1. Define the observable behavior before changing it. Add a regression test for an actual defect.
2. Reuse the service layer for online and upload paths. Never put stock arithmetic in JavaScript.
3. Keep schema changes in numbered migrations. Never edit an already-applied migration to change behavior.
4. Keep data imports and credentials outside Git. Import tools should validate first, write atomically,
   preserve unrelated records and make retries safe.
5. Formatting/static checks are required. Review migrations and financial/stock arithmetic separately.
6. Run PostgreSQL release checks with zero skipped tests. SQLite is only a quick development aid.
7. Independent review remains required for inventory changes, permissions and migrations.
8. Record reviewer, staging evidence and exact commit before production promotion.

## Remaining architectural work

There are still local imports between services/operations/backorders to manage existing dependencies.
This cleanup makes that code readable; it does not claim to remove every dependency cycle.
Extract shared authorization, quantities and orchestration in small separately tested changes.
Do not split the application into microservices for the current ten-user workload.
Adding another customer requires explicit tenancy design and isolation tests first.

## Allocation lock ordering

New orders, multi-store upload submission, accepted-receipt posting and hold release coordinate
through per-part PostgreSQL transaction advisory locks. Acquire all needed gates in sorted key
order before waiting-order/Part/Stock locks. Uploads acquire gates for the whole batch first.
The gates prevent a newly inserted order from missing a receipt's waiting-order snapshot.
Existing fulfillment continues to lock Order, then Part, then Stock; it does not acquire a gate
while holding those row locks. Never introduce the opposite ordering. Advisory locks end at
transaction commit/rollback. SQLite does not provide this guarantee and cannot qualify a release.
