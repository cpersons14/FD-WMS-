import io
from openpyxl import Workbook
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from .models import StoreLocation, Part, Stock, Location, Order, OrderUpload
from .order_upload import preview_upload, submit_upload, HEADERS
from .services import WorkflowError
from .test_receiving import SETTINGS


@override_settings(**SETTINGS)
class OrderUploadTests(TestCase):
    def setUp(self):
        self.fd = get_user_model().objects.create_user("fd")
        self.fd.groups.add(Group.objects.get_or_create(name="FD Corporate")[0])
        self.wh = get_user_model().objects.create_superuser("office")
        self.stores = [
            StoreLocation.objects.create(
                store_number=n,
                address="1 Main St",
                city="Newark",
                state="NJ",
                postal_code="07101",
            )
            for n in ["100", "200"]
        ]
        part = Part.objects.create(sku="001", unit="EA")
        self.stock = Stock.objects.create(
            part=part,
            location=Location.objects.create(code="P1"),
            kind="PICK",
            on_hand=8,
        )
        self.content = b"Store Number,Part Number,Quantity EA,Order Reference\n100,001,5,TEST-A\n200,001,6,TEST-B\n"

    def test_preview_then_multi_store_submit_repeat_once_with_backorder(self):
        batch = preview_upload(self.fd, self.content, "orders.csv")
        self.assertFalse(Order.objects.exists())
        self.assertEqual(len(batch.preview), 2)
        done = submit_upload(self.fd, batch.pk)
        submit_upload(self.fd, batch.pk)
        self.assertEqual(len(done.order_ids), 2)
        self.assertEqual(Order.objects.count(), 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.reserved, 8)
        self.assertEqual(
            sum(
                line.backordered for o in Order.objects.all() for line in o.lines.all()
            ),
            3,
        )
        self.assertEqual(
            preview_upload(self.fd, self.content, "same-file-renamed.csv").pk, batch.pk
        )
        self.assertTrue(all(o.postal_code == "07101" for o in Order.objects.all()))

    def test_invalid_row_blocks_entire_batch(self):
        for bad in [
            b"999,001,1,TEST\n",
            b"100,MISSING,1,TEST\n",
            b"100,001,0.5,TEST\n",
        ]:
            with self.assertRaises(WorkflowError):
                preview_upload(self.fd, self.content + bad, "orders.csv")
        self.assertFalse(OrderUpload.objects.exists())

    def test_changed_address_requires_new_preview_and_submission_is_atomic(self):
        batch = preview_upload(self.fd, self.content, "orders.csv")
        self.stores[1].address = "2 Main St"
        self.stores[1].save()
        with self.assertRaises(WorkflowError):
            submit_upload(self.fd, batch.pk)
        self.assertFalse(Order.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.reserved, 0)
        refreshed = preview_upload(self.fd, self.content, "orders.csv")
        submit_upload(self.fd, refreshed.pk)
        self.assertEqual(Order.objects.get(store="200").address, "2 Main St")

    def test_repeat_item_rows_merge_but_references_separate_orders(self):
        batch = preview_upload(
            self.wh,
            self.content + b"100,001,2,TEST-A\n100,001,1,TEST-C\n",
            "orders.csv",
        )
        self.assertEqual(len(batch.preview), 3)
        submit_upload(self.wh, batch.pk)
        self.assertEqual(Order.objects.get(reference="TEST-A").lines.get().quantity, 7)

    def test_xlsx_and_formula_rejection(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        sheet.append(["100", "001", 2, "TEST"])
        stream = io.BytesIO()
        workbook.save(stream)
        batch = preview_upload(self.fd, stream.getvalue(), "orders.xlsx")
        self.assertEqual(batch.preview[0]["items"], {"001": "2"})
        sheet["C2"] = "=1+1"
        stream = io.BytesIO()
        workbook.save(stream)
        with self.assertRaises(WorkflowError):
            preview_upload(self.fd, stream.getvalue(), "orders.xlsx")

    def test_preview_and_template_pages_and_owner_access(self):
        batch = preview_upload(self.fd, self.content, "orders.csv")
        self.client.force_login(self.fd)
        for url in [
            "/orders/upload/",
            "/orders/upload/template/",
            f"/orders/upload/{batch.pk}/",
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(f"/orders/upload/{batch.pk}/"), "TEST-A")
        self.client.force_login(self.wh)
        self.assertEqual(
            self.client.get(f"/orders/upload/{batch.pk}/").status_code, 404
        )
