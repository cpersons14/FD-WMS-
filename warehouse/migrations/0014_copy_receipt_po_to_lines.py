from django.db import migrations


def copy_po(apps, schema_editor):
    Receipt = apps.get_model("warehouse", "InboundReceipt")
    Line = apps.get_model("warehouse", "InboundReceiptLine")
    for receipt in Receipt.objects.exclude(po_number="").iterator():
        Line.objects.filter(receipt_id=receipt.pk, po_number="").update(
            po_number=receipt.po_number
        )


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0013_receipt_line_po")]
    operations = [migrations.RunPython(copy_po, migrations.RunPython.noop)]
