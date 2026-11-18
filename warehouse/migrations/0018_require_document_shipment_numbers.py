from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0017_populate_document_shipment_numbers")]
    operations = [
        migrations.AlterField(
            model_name="order",
            name="document_number",
            field=models.CharField(editable=False, max_length=32, unique=True),
        ),
        migrations.AlterField(
            model_name="shipment",
            name="requisition",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="shipment",
                to="warehouse.requisition",
            ),
        ),
    ]
