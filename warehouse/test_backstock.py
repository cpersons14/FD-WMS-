import copy
import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from .models import (
    Part,
    Location,
    Stock,
    BackstockReference,
    BackstockImport,
    Order,
    Event,
    StoreLocation,
    OrderUpload,
)
from .backstock import import_locations
from .services import submit_order, WorkflowError
from .order_upload import preview_upload, submit_upload
from .reporting import stock_summary
from .test_receiving import SETTINGS


@override_settings(**SETTINGS)
class BackstockTests(TestCase):
    def setUp(self):
        self.office = get_user_model().objects.create_superuser("office")
        self.fd = get_user_model().objects.create_user("fd")
        self.fd.groups.add(Group.objects.get_or_create(name="FD Corporate")[0])
        self.part = Part.objects.create(sku="PART-A", unit="EA")
        self.stock = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="PICK-A"),
            kind="PICK",
            on_hand=17,
            reserved=3,
        )
        self.payload = dict(
            source_sha256="a" * 64,
            source_name="inventory.xlsx",
            source_sheet="FAMILY DOLLAR",
            ignored_pick_rows=1,
            references=[
                dict(
                    sku="PART-A",
                    description="Sample",
                    location="BACK-1-4",
                    source_rows=[3],
                ),
                dict(
                    sku="UNKNOWN",
                    description="Not in catalog",
                    location="BACK-9",
                    source_rows=[5],
                ),
            ],
        )

    def test_reference_import_changes_no_inventory_or_availability(self):
        before = list(Stock.objects.values())
        totals = [
            {k: v for k, v in row.items() if k != "part"} for row in stock_summary()
        ]
        batch, applied = import_locations(self.office, self.payload)
        self.assertTrue(applied)
        self.assertEqual(batch.added_count, 2)
        self.assertEqual(list(Stock.objects.values()), before)
        self.assertEqual(
            [{k: v for k, v in row.items() if k != "part"} for row in stock_summary()],
            totals,
        )
        self.assertEqual(Part.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(
            BackstockReference.objects.get(sku="PART-A").location, "BACK-1-4"
        )

    def test_repeat_and_later_file_preserve_manual_edits_inactive_and_omitted_refs(
        self,
    ):
        import_locations(self.office, self.payload)
        ref = BackstockReference.objects.get(sku="PART-A")
        ref.active = False
        ref.notes = "Moved; do not use"
        ref.save()
        events = Event.objects.count()
        _, applied = import_locations(self.office, self.payload)
        self.assertFalse(applied)
        self.assertEqual(Event.objects.count(), events)
        next_file = copy.deepcopy(self.payload)
        next_file["source_sha256"] = "b" * 64
        next_file["references"] = next_file["references"][:1]
        batch, _ = import_locations(self.office, next_file)
        ref.refresh_from_db()
        self.assertFalse(ref.active)
        self.assertEqual(ref.notes, "Moved; do not use")
        self.assertEqual(batch.added_count, 0)
        self.assertEqual(batch.existing_count, 1)
        self.assertEqual(BackstockReference.objects.count(), 2)

    def test_quantity_fields_and_missing_rule_parts_reject_whole_import(self):
        bad = copy.deepcopy(self.payload)
        bad["references"][0]["quantity"] = 100
        with self.assertRaises(WorkflowError):
            import_locations(self.office, bad)
        bad = copy.deepcopy(self.payload)
        bad["order_multiples"] = [{"sku": "MISSING", "multiple": 5}]
        with self.assertRaises(WorkflowError):
            import_locations(self.office, bad)
        self.assertFalse(BackstockReference.objects.exists())
        self.assertFalse(BackstockImport.objects.exists())

    def test_warehouse_access_search_edit_and_stale_edit_protection(self):
        import_locations(self.office, self.payload)
        ref = BackstockReference.objects.get(sku="PART-A")
        self.assertEqual(self.client.get("/backstock/").status_code, 302)
        self.client.force_login(self.fd)
        for path in [
            "/backstock/",
            "/backstock/new/",
            "/backstock/import/",
            f"/backstock/{ref.pk}/",
        ]:
            self.assertEqual(self.client.get(path).status_code, 403)
            self.assertEqual(self.client.post(path, {}).status_code, 403)
        self.assertNotContains(self.client.get("/inventory/"), "Backstock locations")
        with self.assertRaises(PermissionError):
            import_locations(self.fd, self.payload)
        self.client.force_login(self.office)
        for query in ["PART-A", "Sample", "BACK-1-4"]:
            self.assertContains(
                self.client.get("/backstock/", {"q": query}), "BACK-1-4"
            )
        self.assertContains(self.client.get("/inventory/", {"q": "BACK-1-4"}), "PART-A")
        self.assertContains(
            self.client.get("/inventory/", {"q": "PART-A"}),
            "1 backstock location reference",
        )
        data = {
            "sku": ref.sku,
            "description": ref.description,
            "location": ref.location,
            "notes": "No longer here",
            "revision": 0,
        }
        self.assertEqual(
            self.client.post(f"/backstock/{ref.pk}/", data).status_code, 302
        )
        ref.refresh_from_db()
        self.assertFalse(ref.active)
        self.assertEqual(ref.revision, 1)
        self.assertContains(
            self.client.post(f"/backstock/{ref.pk}/", data),
            "changed while you were editing",
        )
        ref.refresh_from_db()
        self.assertEqual(ref.revision, 1)
        self.assertContains(self.client.get("/history/"), "BACKSTOCK_REFERENCE_EDITED")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.on_hand, 17)
        self.assertEqual(self.stock.reserved, 3)


@override_settings(**SETTINGS)
class OrderIncrementTests(TestCase):
    def setUp(self):
        self.office = get_user_model().objects.create_superuser("office")
        self.fd = get_user_model().objects.create_user("fd")
        self.fd.groups.add(Group.objects.get_or_create(name="FD Corporate")[0])
        self.part = Part.objects.create(sku="RULE-A", unit="EA", order_multiple=2)
        self.cart = Part.objects.create(sku="RULE-B", unit="EA", order_multiple=5)
        self.stock = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="P1"),
            kind="PICK",
            on_hand=3,
        )
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
        self.details = self.stores[0].order_details()

    def test_online_staff_and_customer_reject_invalid_accept_valid_without_rounding(
        self,
    ):
        for actor in [self.office, self.fd]:
            for sku, amount in [
                ("RULE-A", 1),
                ("RULE-A", 3),
                ("RULE-B", 4),
                ("RULE-B", 6),
            ]:
                with self.assertRaisesRegex(WorkflowError, "multiples of"):
                    submit_order(
                        actor, self.details, [(sku, amount, "EA")], uuid.uuid4()
                    )
        self.assertFalse(Order.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.reserved, 0)
        order = submit_order(
            self.fd,
            self.details,
            [("RULE-A", 4, "EA"), ("RULE-B", 5, "EA")],
            uuid.uuid4(),
        )
        line = order.lines.get(part=self.part)
        self.assertEqual(line.quantity, 4)
        self.assertEqual(line.backordered, 1)
        self.assertEqual(order.lines.get(part=self.cart).quantity, 5)

    def test_upload_preview_and_submission_recheck_rules_atomically(self):
        content = b"Store Number,Part Number,Quantity EA,Order Reference\n100,RULE-A,2,A\n200,RULE-B,6,B\n"
        with self.assertRaisesRegex(WorkflowError, "Row 3, store 200"):
            preview_upload(self.fd, content, "orders.csv")
        self.assertFalse(OrderUpload.objects.exists())
        self.assertFalse(Order.objects.exists())
        content = content.replace(b"RULE-B,6", b"RULE-B,5")
        batch = preview_upload(self.fd, content, "orders.csv")
        self.cart.order_multiple = 10
        self.cart.save()
        with self.assertRaisesRegex(WorkflowError, "multiples of 10"):
            submit_upload(self.fd, batch.pk)
        self.assertFalse(Order.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.reserved, 0)
        self.cart.order_multiple = 5
        self.cart.save()
        submit_upload(self.fd, batch.pk)
        self.assertEqual(Order.objects.count(), 2)

    def test_private_rules_do_not_rewrite_existing_order_or_quantity_and_repeat_is_noop(
        self,
    ):
        self.part.order_multiple = 1
        self.part.save()
        old = submit_order(
            self.office, self.details, [("RULE-A", 1, "EA")], uuid.uuid4()
        )
        before = list(Stock.objects.values())
        payload = dict(
            source_sha256="c" * 64,
            source_name="inventory.xlsx",
            source_sheet="FAMILY DOLLAR",
            ignored_pick_rows=0,
            references=[
                dict(sku="RULE-A", description="", location="BACK-1", source_rows=[2])
            ],
            order_multiples=[
                dict(sku="RULE-A", multiple=2),
                dict(sku="RULE-B", multiple=5),
            ],
        )
        import_locations(self.office, payload)
        self.assertEqual(old.lines.get().quantity, 1)
        self.assertEqual(list(Stock.objects.values()), before)
        self.part.refresh_from_db()
        self.assertEqual(self.part.order_multiple, 2)
        self.part.order_multiple = 4
        self.part.save()
        import_locations(self.office, payload)
        self.part.refresh_from_db()
        self.assertEqual(self.part.order_multiple, 4)

    def test_rule_hints_are_visible_to_customer_and_office(self):
        for actor in [self.office, self.fd]:
            self.client.force_login(actor)
            self.assertContains(
                self.client.get("/orders/new/"), 'data-order-multiple="2"'
            )
            self.assertContains(self.client.get("/orders/upload/"), "multiples of 5 EA")
