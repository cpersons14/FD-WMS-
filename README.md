# Family Dollar WMS

Office-driven receiving, inventory, ordering and fulfillment for S3PL / Family Dollar.
Python 3.12, Django 5.2 and PostgreSQL 18. This is a single-customer pilot.

Application source lives in `config/`, `warehouse/`, `templates/` and `static/`.
Builds install dependencies and collect static files; they do not reconstruct source code.
`build (3).sh` temporarily forwards to `build.sh` for the existing Render command.

## Quality checks

Install `requirements-dev.txt`. Start disposable PostgreSQL with:

```sh
docker compose -f compose.test.yml up -d --wait
export WMS_TEST_DATABASE_URL=postgresql://wms_test:disposable-local-only@127.0.0.1:55432/wms_test_local
export DJANGO_SETTINGS_MODULE=config.test_settings
ruff check config warehouse scripts manage.py
ruff format --check config warehouse scripts manage.py
python scripts/check_repository.py
coverage run manage.py test warehouse --noinput
coverage report
python manage.py makemigrations --check --dry-run
```

PowerShell uses `$env:WMS_TEST_DATABASE_URL="..."` and `$env:DJANGO_SETTINGS_MODULE="config.test_settings"`.
The runner requires local disposable PostgreSQL and fails on skipped tests. It cannot use the
production database URL. Tests use synthetic data; no customer mail is sent.

GitHub Actions runs these checks on pull requests and main. An administrator must separately
require the check in branch rules; adding a workflow does not protect main by itself.

## Deployment and operations

- [Architecture and rules](docs/ARCHITECTURE.md)
- [Release, staging and independent review](docs/RELEASE.md)
- [Recovery and backup verification](docs/RECOVERY.md)
- [Validation evidence](docs/VALIDATION.md)
- [Office receiving workflow](RECEIVING_WORKFLOW.md)

`render.yaml` describes the existing app. `render.staging.yaml` describes separate paid resources
and must be reviewed before applying. Health endpoint: `/health/ready/` (minimal 200/503 response).

Never commit inventory, order/customer files, attachments, secrets or database backups.
Do not add another customer's inventory until tenancy and permissions have been designed and tested.
New backstock and order-increment code is included; the approved data payload is transferred separately.

A passing test suite is evidence, not a guarantee of correctness. The release checklist requires
operator acceptance, a verified restore and independent code review before customer launch.
