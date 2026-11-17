from django.db import migrations
from django.db.models import F


def existing_acceptance(apps, schema_editor):
    Line = apps.get_model("warehouse", "InboundReceiptLine")
    Line.objects.filter(
        receipt__status="POSTED", held=False, accepted_quantity=0
    ).update(accepted_quantity=F("quantity"))


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0010_archive_superseded_bins")]
    operations = [migrations.RunPython(existing_acceptance, migrations.RunPython.noop)]
