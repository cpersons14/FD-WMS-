# Release procedure

## First migration away from uploaded installers

This branch includes the previously prepared backstock and order-increment code.
The private 370-reference payload remains outside Git and has not been loaded into production.

1. Import the reviewed branch into GitHub and open a pull request against `main`.
2. Let `WMS quality / PostgreSQL release checks` run and require a successful result.
3. Configure a `main` branch rule requiring pull requests, one independent approving review,
   resolved discussions, and the exact check name `PostgreSQL release checks`.
   Require the branch to be current; disable force pushes/deletions and do not bypass checks.
   **The workflow file alone does not enable this rule. A repository admin must apply it.**
4. Review the removed root copies. Runtime files now live in their normal directories.
   `build (3).sh` is only a small forwarder to `build.sh`, preserving the existing Render command.
5. Complete the staging and recovery gates below before deploying to the existing customer portal.
6. After the clean source is live, Render's build command can be changed to `bash build.sh`.
   Retire the forwarding file in a later small PR. Never upload an old installer over clean source.

## Staging

- `render.staging.yaml` defines a separate app and PostgreSQL 18 database in Ohio.
- It creates paid resources; review the displayed cost before applying it.
- Publish source first. Blueprint path is `render.staging.yaml`, repository `cpersons14/FD-WMS-`.
- Use a distinct admin account, synthetic inventory and `.example`/`example.com` recipients only.
- Set `WMS_ENVIRONMENT=staging`. The app rejects any database name except `s3pl_fd_staging`,
  rejects production import payloads and disables customer emails regardless of email flags.
- Do not copy the production environment group or production database URL.
- Run scripts/tests against disposable local PostgreSQL; never run Django test teardown using a
  production or shared staging account. Staging is for operator acceptance testing.
- Keep the same reviewed commit for staging and production. Automatic deployment stays off.

## Acceptance evidence

Have office and floor staff complete these scenarios and record expected/actual results:

| Scenario | Required result |
| --- | --- |
| New receipt, attachment, draft correction, posting, retry | One posting; intact attachment; no duplicate stock |
| Two simultaneous posts of one receipt | One inventory increase and one email record |
| Ten users request scarce stock | Total reservations never exceed stock; shortages become backorders |
| Partial pick/pack/ship, later accepted receipt | Remaining amount releases once; shipped amount unchanged |
| Held/damaged receipt and accepted PO quantities | Held stock unavailable; only accepted quantities advance PO |
| Online and multi-store upload | Same EA/increment rules; invalid upload rolls back completely |
| Customer vs warehouse login | Customer cannot count, receive, fulfill or fetch warehouse documents |
| Backstock import/re-import/edit | No balance changes; edits and inactive references preserved |
| Item pack change after a receipt/order | Historical conversion remains unchanged |
| Cancel, unpick and return | Physical state and audit trail reconcile |

## Production promotion

- Named reviewer approved the exact commit; CI passed; office acceptance recorded.
- Current backup/recovery window inspected and a restore drill completed (see RECOVERY.md).
- Record database counts and totals before a schema/data change; review migration plan.
- Set Render health path to `/health/ready/` only once the endpoint is in the deployed commit.
- Build: `bash build.sh` (old forwarding command remains compatible during transition).
- Pre-deploy: `python manage.py migrate --noinput && python manage.py setup_wms`.
- Keep existing private import environment values only when their reviewed, idempotent imports
  are intended. Never replace all environment variables to add one value.
- Monitor the exact deploy to `live`, then verify login, CSS/assets, authorized inventory and health.
- Confirm email sending/recipients separately before customer rollout.
- Preserve pilot balances until the approved physical inventory cutover; this release does not reset stock.

## Alerts and ownership

Render currently has no health-check path configured. The new configuration is prepared, not applied.
Configure uptime/error notifications to the designated S3PL owner; inspect failed deploy notices,
HTTP 5xx logs, database capacity and receipt-email failures. Test alert delivery to an internal recipient.
The new health endpoint and console logs supply signals; they do not create an alert subscription.
Assign a primary maintainer and a backup person. Log issues and regressions in GitHub.

The deployment check intentionally excludes `security.W021` (optional HSTS preload enrollment)
and the existing `axes.W006` proxy/IP warning. HTTPS, secure cookies and HSTS remain on.
Confirm trusted-proxy handling and edge IP throttling before customer launch; account-based login
lockout is already tested. Preload enrollment requires a separate domain-owner decision.

## Independent review handoff

Give a Django/PostgreSQL reviewer the PR diff, ARCHITECTURE.md, test output and this checklist.
Ask them to examine locking/order races, quantity conservation, authorization, file handling,
historical migrations, email retry behavior and recovery. Author review and AI-generated tests
are not independent sign-off. No reviewer has been contacted or approval obtained by this change.
