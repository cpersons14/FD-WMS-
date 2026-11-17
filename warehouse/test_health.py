from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=True)
class ReadinessTests(TestCase):
    def test_health_is_minimal_uncached_and_available_to_render(self):
        response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.head("/health/ready/").status_code, 200)

    def test_failure_has_no_connection_details(self):
        with patch(
            "warehouse.health.connection.cursor", side_effect=OperationalError("secret")
        ):
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn(b"secret", response.content)

    def test_health_has_no_write_method_and_other_pages_require_https(self):
        self.assertEqual(self.client.post("/health/ready/").status_code, 405)
        self.assertEqual(self.client.get("/login/").status_code, 301)
