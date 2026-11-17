"""Release checks fail when PostgreSQL coverage is silently skipped."""

from django.db import connection
from django.test.runner import DiscoverRunner


class PostgresReleaseRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        if connection.vendor != "postgresql":
            raise RuntimeError("Release tests require PostgreSQL.")
        return super().setup_databases(**kwargs)

    def suite_result(self, suite, result, **kwargs):
        failures = super().suite_result(suite, result, **kwargs)
        if result.skipped:
            self.log("Release blocked: skipped tests are not allowed.")
        return failures + len(result.skipped)
