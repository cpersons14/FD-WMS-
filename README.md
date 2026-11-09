# S3PL Family Dollar WMS — office receiving pilot

A standalone Python/Django application with a private customer portal and PostgreSQL database. Deploy using START_HERE.html and the included render.yaml. This upload copy excludes real inventory and starts empty. This is an expanded pilot, not a completed production rollout or a multi-customer platform.

## Implemented

- Named users; FD Corporate can see FD inventory and all FD orders and submit requests. S3PL Warehouse can fulfill orders and manage stock. No public signup. Five failed logins lock the account for 15 minutes.
- Item and bin setup; pick and overstock locations; searchable SKU/barcode; explicit base-unit field; uncounted quantities distinct from zero; CSV inventory export with spreadsheet formula escaping.
- Office New Receipt: delivery header, multiple item/location rows keyed from the paper putaway sheet, drafts/clarification queue, bounded private PDF/JPG/PNG attachments, posted receipt history and one grouped customer email per delivery. Posting and email retries are separate; posted documents are locked. See RECEIVING_WORKFLOW.md.
- Each/case/standard-pallet entry with confirmed case pack, loose eaches, on-screen each totals, conversion audit and immutable order/receipt pack snapshots. Existing balances are never rescaled automatically.
- Physical counts with reason, count dates and configurable count interval; an audited stock ledger; quality holds that exclude stock from allocation.
- Warehouse task list for pick replenishment, due counts, held stock and backorders. Replenishment is a suggestion; staff choose and confirm the actual transfer. Reservations follow moved stock.
- Direct corporate orders, urgent priority, requested ship date, optional backorders (checked by default in the order form), stock reservations, partial picks and explicit reallocation of backorders after receipts.
- Part/barcode and quantity pack verification, staged quantity per order line, partial shipments with their own carrier/tracking/PRO and quantities, printable pick and shipment packing records.
- Unpack and unpick reversals with reasons; cancellation before picking or cancellation of the unpicked remainder; bounded returns into counted quarantine bins and inspection before hold release.
- Per-item low-stock thresholds, target stock and pick minimums. Portal alerts and opt-in daily email digest for active FD Corporate accounts with delivery records. Stock and receipt email are independently disabled by default.
- Transaction locks, quantity constraints, and browser action keys protect against overselling and repeated receiving, transferring, picking, packing, shipping and return submissions. PostgreSQL concurrency must still be validated in staging.

## Stock definitions

Bin on hand includes physical stock in storage bins, including held stock. Uncounted balances are flagged and excluded from availability. Available = counted, unheld bin on hand minus reservations. Picked stock leaves the storage bin and becomes staged on the order. Staged = picked minus shipped. It cannot be ordered again. Packed and shipped quantities cannot exceed picked quantities. Returns add physical stock to a held quarantine bin, not to immediately available stock.

Staging is tracked by order line, not by physical staging/tote location. Partial shipping ships all packed-but-unshipped quantities on the order as one shipment. Multiple parcels within a shipment, weight/dimension capture and carrier labels are not implemented. A keyboard-style scanner can populate SKU/barcode fields; no device, camera or label-printer integration is installed.

A low-stock alert appears when available <= the configured threshold, including zero. A blank threshold means not configured. Unknown or held stock is reported alongside the warning. Target restock suggestion = max(0, target - available + unallocated backorders). It excludes expected inbound supply; there is no purchasing/ASN integration. It never sends purchase orders.

Base unit is blank until verified. Confirm EA only when the existing stock values already represent eaches; otherwise reconcile them before conversion. Set confirmed eaches per case and, optionally, cases per standard pallet in Item settings. Eaches, cases and pallets must be whole quantities; additional loose eaches support partial packs. Orders retain their original factors even after item-master changes. A case-equivalent display is arithmetic, not proof of unopened physical cases. Source location strings remain unchanged, even if they span multiple positions; review location naming before launch. Legacy orders preserve their old base unit and quantities; do not invent missing historical pack sizes.

## First deployment and later inventory import

This deployment copy contains no real inventory dataset, customer orders, credentials or seeded users. Automated tests use synthetic records. Start with an empty database and load verified stock later through an approved private transfer, not GitHub.

Upload the extracted contents of this folder directly to the root of a private GitHub repository. The repository's main page must show `render.yaml`, `manage.py`, `requirements.txt`, `config/`, and `warehouse/`. Upload the individual files and folders, not the ZIP or its enclosing folder.

In Render choose New → Blueprint, select the repository, select branch `main` (or your actual uploaded branch), and use Blueprint Path `render.yaml`. The deployment creates the web service and PostgreSQL database together. Review the displayed charges before deploying. The pre-deploy command runs migrations and creates the FD Corporate/S3PL Warehouse roles; it creates no inventory or user accounts.

After the service is Live, open the web service's Shell and run:

```sh
python manage.py createsuperuser --username colin
```

Choose your email and password in the Shell. Sign in at `/admin/` to add individually named pilot users and assign their groups. Add sample parts/bins through Inventory for a controlled trial. Email remains disabled until configured. See START_HERE.html.

For later approved opening inventory, keep the JSON outside the repository and run `python manage.py import_fd /private/path/verified_inventory.json --actor colin` with the actual private path. Import requires a superuser and refuses to overwrite an existing stock database. Do not import into a database containing pilot transactions; use a clean production database after reconciliation.

For an existing pilot, back up before upgrading. Deploy new code and run migrations; do not rerun opening inventory. Historical orders retain their quantities. Reconcile the upgraded ledger before live use; rollback requires a verified backup.

## Customer alerts — IT setup

1. Warehouse opens Inventory → Item settings. Confirm base unit, low-stock threshold, target, optional pick minimum and count interval. Thresholds are not guessed from an uncounted opening snapshot.
2. Administrator assigns each approved person an individual portal account and approved email address. Each intended FD Corporate user opens Stock alerts and opts in to the daily summary. Warehouse-only accounts cannot receive the customer digest. FD Corporate users see this customer's whole inventory; there is no arbitrary external recipient entry.
3. Configure an approved SMTP sender using Render environment variables: EMAIL_HOST, EMAIL_PORT (default 587), EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS=1, DEFAULT_FROM_EMAIL. Keep secrets out of GitHub.
4. Preview from the service Shell: `python manage.py stock_digest --dry-run`. This prints the digest but sends nothing and consumes no delivery record.
5. Only after sender and recipients are approved, set WMS_EMAIL_ENABLED=1. Test an email to an internal pilot account before customer enablement.
6. Add a Render Cron Job using the same private repository and region, build command `pip install -r requirements.txt`, start command `python manage.py stock_digest`. Copy the required DATABASE_URL, SECRET_KEY, DEBUG=0 and SMTP settings into that job (or use a shared environment group). Use the same database as the selected WMS environment.
7. Example schedule: `0 14 * * *` runs at 14:00 UTC, 8 a.m. Central Standard Time / 9 a.m. Central Daylight Time. IT should set the desired time. Render cron schedules use UTC and have a minimum monthly charge of $1 per cron service; email-provider charges are separate. No cron job or paid service has been created by this package.
8. Monitor exit status and Alert deliveries in /admin/. One digest per opted-in active FD Corporate account per America/Chicago date, only when configured items are low. Each recipient gets a separate message. Removed access/disabled accounts are excluded. The next day's summary reflects current balances, so restocked items disappear.

A send is claimed before contacting SMTP. Failure or a crash leaves FAILED or SENDING visible, and the same day's job will not blindly resend. Investigate with the provider before any manual recovery. Successful provider acceptance is recorded as SENT; it does not guarantee inbox delivery. Delivery errors store exception class, not credentials. No external email has been sent during development.

## Receiving email and documents — IT setup

1. Migrations create an empty **FD Receiving** group. In `/admin/`, open Receipt groups and enter only approved FD distribution/member addresses. This group is separate from the individual low-stock digest subscriptions. No customer addresses are hard-coded.
2. Configure the approved SMTP sender using the email variables above. Receipt email uses a separate switch: `WMS_RECEIPT_EMAIL_ENABLED=1`. Leave it off until recipient configuration and an internal acceptance test are complete. `WMS_EMAIL_ENABLED` controls only the daily stock digest.
3. Office submits the delivery from Receiving. Inventory commits first, then the request attempts one email. The receipt detail shows pending, sending, sent or failed and a preview of its exact body/recipient/document snapshot. Email-provider acceptance is not proof of inbox delivery.
4. Pending emails can be sent from the receipt detail. `python manage.py receipt_emails --dry-run` lists pending messages without sending; `python manage.py receipt_emails` processes only pending messages. IT may schedule the latter for crash recovery; no scheduler is provisioned by this package. Failed or uncertain sends require office review and explicit retry, and never alter inventory. Do not blindly reset delivery statuses in the database.
5. Before a retry after failure, confirm the previous attempt was not delivered. Active sends cannot be retried for five minutes. If the approved group changed after posting, the snapshot is blocked for review; there is not yet an audited recipient-redirection tool. Do not restore revoked recipients simply to bypass this block. IT must reconcile any blocked legacy outbox entry before cutover.
6. Attachments are stored privately in PostgreSQL, within the per-receipt limits, so they survive application redeployment and are included in database backups. There is no public media directory. Warehouse accounts can view/download documents; customer logins cannot browse internal receipt documents. Selected files reach FD by email. Monitor database growth and backup size; larger volumes warrant private object storage. Validate real PDF/image handling and the company's malware-scanning policy before production; extension/signature checks are not an antivirus scanner.
7. Receipt reference values are not globally unique: separate deliveries can reuse a BOL/reference. Idempotency protects repeated submission of the same receipt; office must search the receipt queue to avoid entering the same physical delivery as a new receipt.

## Local development and validation

Python 3.12 recommended. Create a virtual environment, install requirements.txt, set DEBUG=1, then run migrations. SQLite is for local testing only. Production requires DATABASE_URL for PostgreSQL and SECRET_KEY.

```sh
DEBUG=1 python manage.py test warehouse
DEBUG=1 python manage.py makemigrations --check --dry-run
```

Run PostgreSQL tests with a separate disposable database and a test role permitted to create a test database. Never use the production database account. See VALIDATION.txt for checks actually run.

## Production work still required

- S3PL/FD acceptance tests with actual receiving, short picks, mixed shipments, returns, corrections and simultaneous users; verified counts, units and store addresses; independent code/security review.
- PostgreSQL concurrency and load tests, backup restore drill, monitoring, an owner for maintenance, SSO/MFA decision, edge IP throttling, notification sender/recipient configuration, coordinated cutover and rollback.
- Decide requirements for advance receiving notices/purchase orders, directed putaway, serial/expiration tracking if needed, varying case packs within stock, physical staging locations, waves/batch picking, multi-customer data separation, damaged-stock disposal, billing and carrier connections. These are NOT implemented by this build. Lot tracking was explicitly not required for the agreed FD workflow.
- UPS WorldShip / Freightview export and tracking synchronization, automatic customer shipment emails, customer order amendments, and production barcode/label devices need their own integration work. Do not infer these from the shipment-recording screen.

All writes are customer-specific to FD in this release. Do not add another customer's inventory until tenancy and access separation are implemented and tested.

Official references checked November 2026:
https://render.com/docs/deploy-django
https://render.com/docs/cronjobs
https://render.com/docs/postgresql-backups
https://docs.djangoproject.com/en/5.2/topics/email/
