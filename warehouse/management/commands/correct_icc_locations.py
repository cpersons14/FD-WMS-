from collections import Counter
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from warehouse.icc_locations import correct_locations


class Command(BaseCommand):
    help = "Preview/apply approved ICC bin corrections; skip records with activity."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            actor = get_user_model().objects.get(username=options["actor"])
        except get_user_model().DoesNotExist:
            raise CommandError("User not found.")
        try:
            rows = correct_locations(actor, options["apply"])
        except PermissionError as exc:
            raise CommandError(str(exc))
        for row in rows:
            if row["status"] != "UNCHANGED":
                self.stdout.write(f"{row['status']} | {row['sku']} | {row['reason']}")
        self.stdout.write(str(dict(Counter(r["status"] for r in rows))))
        if not options["apply"]:
            self.stdout.write(
                "Preview only. Add --apply to make the listed READY corrections."
            )
