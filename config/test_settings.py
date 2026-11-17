"""Disposable local/CI PostgreSQL only; never inherit production credentials."""

import os
from urllib.parse import urlparse

url = os.environ.get("WMS_TEST_DATABASE_URL", "")
parsed = urlparse(url)
if (
    parsed.scheme not in {"postgres", "postgresql"}
    or parsed.hostname not in {"localhost", "127.0.0.1", "::1", "postgres"}
    or not parsed.path.removeprefix("/").startswith("wms_test_")
):
    raise RuntimeError(
        "Tests require WMS_TEST_DATABASE_URL pointing to local PostgreSQL "
        "and a database whose name starts with wms_test_."
    )

os.environ["DATABASE_URL"] = url
os.environ["DB_SSLMODE"] = "disable"  # Only the local disposable database above.
os.environ["DEBUG"] = "0"
os.environ["WMS_ENVIRONMENT"] = "production"
os.environ["SECRET_KEY"] = "test-only-key-not-used-by-any-live-service-0123456789abcdef"
for name in ("S3PL_FD_BIN_APPROVAL_JSON", "S3PL_FD_BACKSTOCK_JSON"):
    os.environ.pop(name, None)

from .settings import *  # noqa: F403

SECURE_SSL_REDIRECT = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
WMS_EMAIL_ENABLED = False
WMS_RECEIPT_EMAIL_ENABLED = False
TEST_RUNNER = "warehouse.test_runner.PostgresReleaseRunner"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
