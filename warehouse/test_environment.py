"""Deployment guards must fail before contacting an unintended database."""

import os
import subprocess
import sys

from django.test import SimpleTestCase


class EnvironmentGuardTests(SimpleTestCase):
    def settings_process(self, **changes):
        env = {
            "PATH": os.environ["PATH"],
            "SECRET_KEY": "test-only-configuration-check-0123456789abcdefghijklmnopqrstuvwxyz",
            "DATABASE_URL": "postgresql://unused:unused@localhost/s3pl_fd_staging",
            "WMS_ENVIRONMENT": "staging",
            **changes,
        }
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from config import settings as s; assert not s.WMS_EMAIL_ENABLED; assert not s.WMS_RECEIPT_EMAIL_ENABLED; assert s.EMAIL_BACKEND.endswith('dummy.EmailBackend')",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_staging_rejects_production_database(self):
        result = self.settings_process(
            DATABASE_URL="postgresql://unused@localhost/s3pl_fd"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("separate s3pl_fd_staging database", result.stderr)

    def test_staging_forces_email_off(self):
        result = self.settings_process(
            WMS_EMAIL_ENABLED="1", WMS_RECEIPT_EMAIL_ENABLED="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_rejects_production_import_payload(self):
        result = self.settings_process(S3PL_FD_BACKSTOCK_JSON="{}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production import configuration", result.stderr)

    def test_test_settings_reject_remote_or_production_database(self):
        for url in (
            "postgresql://unused@production.example/wms_test_ci",
            "postgresql://unused@127.0.0.1/s3pl_fd",
        ):
            result = subprocess.run(
                [sys.executable, "-c", "import config.test_settings"],
                env={"PATH": os.environ["PATH"], "WMS_TEST_DATABASE_URL": url},
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Tests require WMS_TEST_DATABASE_URL", result.stderr)
