# Backup and recovery runbook

Current production resource: Render `s3pl-fd-db`, PostgreSQL 18, Ohio.
This change does not modify that database or its network allowlist.

Render documents continuous point-in-time backups for paid PostgreSQL. The available window depends
on the workspace plan. Verify the actual recoverable window in the database's Recovery/Backups screen;
do not assume the compute plan establishes retention or that a backup has been successfully restored.
Reference: https://render.com/docs/postgresql-backups

## Required production-data restore drill (still pending)

1. Record the database ID, current recovery window, selected recovery point and exact app commit.
2. Arrange an isolated recovery target through Render's recovery flow. Inspect the plan before
   confirming; it must create a separate target and must not overwrite the live database.
3. Keep the recovered database private. Do not connect customer traffic or email workers to it.
4. Verify schema/migrations, user and role counts, parts/bins, balances and reservations, orders,
   shipment history, receipt attachments (including hashes), audit rows and email-outbox states.
5. Start a recovery application with email disabled and no production import configuration. Verify
   login and read-only workflows, then run synthetic operations only in the isolated recovery copy.
6. Record elapsed restore time, checks, recovery-point lag, owner and result. Choose acceptable
   recovery-time/data-loss targets with operations. Do not mark the drill complete without evidence.

## Code rollback and database rollback are different

- Stop writes and preserve evidence if quantities or schema appear wrong.
- A Render rollback changes application code; it does not reverse migrations or restore inventory.
- Prefer a reviewed forward fix when the schema/data remains intact.
- Only restore to a verified recovery point after identifying transactions since that point and
  agreeing how to recover them. Never silently discard orders received after a backup.
- Do not roll back to a version whose models cannot read the current database schema.
- Keep imports idempotent. Never rerun opening inventory as a rollback technique.

## Local tool rehearsal

`scripts/restore_drill.py` is limited to disposable local PostgreSQL databases beginning `wms_test_`.
It creates a new target, restores a custom-format dump, compares all public-table contents and sequence
states, and leaves the target available for inspection. It refuses existing targets and remote hosts.
It does not test Render's production backups, provider recovery flow or off-machine retention.
Keep all dump files out of Git. Delete local synthetic artifacts when no longer required.
