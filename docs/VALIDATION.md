# Validation evidence — 2026-11-13

Tested source: the reliability branch including the pending backstock/order-increment update. These changes are not deployed.

| Check | Observed result |
| --- | --- |
| Full suite, PostgreSQL 18.7 / Python 3.12 | 115 tests passed; zero skipped; 22.079 seconds |
| Prior PostgreSQL-only tests | Last-stock reservations, duplicate delivery posting, overlapping receipt releases passed |
| Ten simultaneous orders | Five units allocated once, five units backordered; stock conserved |
| Receipt/new-order race | Failed before fix (5 available / 5 backordered); passed after coordination fix |
| Migration drift | No changes detected |
| Migrations and role setup | Passed on disposable PostgreSQL |
| Static analysis and formatting | Ruff passed |
| Production configuration check | Passed with documented AXES and HSTS-preload exceptions |
| Render production/staging YAML | Validated against current public Render JSON schema; not applied |
| GitHub workflow | Validated against current workflow schema; not executed on GitHub yet |
| Local synthetic backup/restore | 40 tables and all sequence states matched, including private attachment bytes |
| Coverage | 77% combined statement/branch coverage; this is not independent review or proof of correctness |

## Important boundaries

- No production inventory, user accounts, credentials, emails or database access rules were changed.
- The normal application still has local imports/dependency cycles; readability and source organization improved, but a full domain refactor is not claimed.
- Test coverage is weakest around file-parser edge cases, one-off import commands and some view/error branches. Historical private-data import checks were run separately; they are not counted in this coverage total.
- Hosted staging, GitHub branch-rule enforcement, Render alert delivery, real-backup restore and independent human review are still pending.
- A ten-thread transaction test validates the modeled contention scenario; it is not a full network/browser performance benchmark.
- Render public JSON-schema validation is not account-aware Blueprint validation. Review the Blueprint plan before creation.

## Evidence files

Full test output, coverage summary, build/check logs and the red/green race result are included in the handoff package. All test data is synthetic.
