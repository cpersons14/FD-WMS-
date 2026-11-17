import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import OrderForm
from .models import StoreLocation, Order, Part, Location, Stock, Event
from .store_directory import import_report

ROW = dict(
    store_number="20890",
    address="3947 FEDERAL ST",
    city="PENNSAUKEN",
    state="NJ",
    postal_code="08105-2636",
    county="Camden County",
    location_type="Strip",
)


@override_settings(
    STORAGES={
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        }
    },
    AXES_ENABLED=False,
)
class StoreDirectoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.office = User.objects.create_user("office")
        self.office.groups.add(Group.objects.create(name="S3PL Warehouse"))
        self.customer = User.objects.create_user("fd")
        self.customer.groups.add(Group.objects.create(name="FD Corporate"))
        self.other = User.objects.create_user("other")
        self.store = StoreLocation.objects.create(**ROW)

    def test_search_requires_portal_role_and_import_requires_warehouse(self):
        self.assertEqual(
            self.client.get(reverse("store_search"), {"q": "20890"}).status_code, 302
        )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("store_search"), {"q": "20890"}).status_code, 403
        )
        self.client.force_login(self.customer)
        self.assertEqual(
            self.client.get(reverse("store_search"), {"q": "20890"}).status_code, 200
        )
        self.assertEqual(self.client.get(reverse("directory_import")).status_code, 403)
        self.client.force_login(self.office)
        self.assertEqual(self.client.get(reverse("directory_import")).status_code, 200)

    def test_zip_preserves_zero_and_returns_choices_excluding_other_customer_and_inactive(
        self,
    ):
        StoreLocation.objects.create(**(ROW | {"store_number": "20891"}))
        StoreLocation.objects.create(
            **(ROW | {"store_number": "20892", "active": False})
        )
        StoreLocation.objects.create(**ROW, customer_code="OTHER")
        self.client.force_login(self.customer)
        r = self.client.get(reverse("store_search"), {"q": "08105"})
        self.assertEqual(len(r.json()["results"]), 2)
        self.assertEqual(r.json()["results"][0]["postal_code"], "08105-2636")
        self.assertIn("no-store", r["Cache-Control"])

    def test_search_by_city_address_county_and_store_and_limit(self):
        self.client.force_login(self.customer)
        for query in ["#20890", "2089", "Pennsauken NJ", "FEDERAL", "Camden"]:
            self.assertEqual(
                self.client.get(reverse("store_search"), {"q": query}).json()[
                    "results"
                ][0]["id"],
                self.store.pk,
            )
        for n in range(30):
            StoreLocation.objects.create(**(ROW | {"store_number": str(30000 + n)}))
        data = self.client.get(reverse("store_search"), {"q": "NJ"}).json()
        self.assertEqual(len(data["results"]), 20)
        self.assertTrue(data["more"])
        self.assertEqual(
            self.client.get(reverse("store_search"), {"q": ""}).json()["results"], []
        )

    def test_single_digit_and_padded_store_search(self):
        store = StoreLocation.objects.create(**(ROW | {"store_number": "1"}))
        self.client.force_login(self.customer)
        for query in ["1", "#1", "00001"]:
            self.assertIn(
                store.pk,
                [
                    row["id"]
                    for row in self.client.get(
                        reverse("store_search"), {"q": query}
                    ).json()["results"]
                ],
            )

    def test_selected_store_fills_server_fields_and_order_keeps_snapshot(self):
        self.client.force_login(self.customer)
        part = Part.objects.create(sku="TEST-PART", unit="EA")
        Stock.objects.create(
            part=part,
            location=Location.objects.create(code="P1"),
            kind="PICK",
            on_hand=10,
        )
        payload = {
            "store_location": self.store.pk,
            "store": "WRONG",
            "address": "TAMPERED",
            "priority": "NORMAL",
            "request_key": uuid.uuid4(),
            "sku": part.sku,
            "quantity": "2",
            "unit": "EA",
        }
        response = self.client.post(reverse("new_order"), payload)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        for field, value in self.store.order_details().items():
            self.assertEqual(getattr(order, field), value)
        self.store.address = "NEW ADDRESS"
        self.store.save()
        order.refresh_from_db()
        self.assertEqual(order.address, ROW["address"])
        self.assertContains(
            self.client.get(reverse("order_detail", args=[order.pk])), ROW["county"]
        )

    def test_invalid_or_inactive_or_other_customer_selection_is_rejected(self):
        self.client.force_login(self.customer)
        other = StoreLocation.objects.create(**ROW, customer_code="OTHER")
        self.store.active = False
        self.store.save()
        for value in ["not-an-id", "999999999", str(other.pk), str(self.store.pk)]:
            form = OrderForm(
                {
                    "store_location": value,
                    **self.store.order_details(),
                    "priority": "NORMAL",
                }
            )
            self.assertFalse(form.is_valid())
            self.assertIn("store_location", form.errors)
        self.assertFalse(Order.objects.exists())

    def test_manual_delivery_still_works(self):
        form = OrderForm(
            {**self.store.order_details(), "store_location": "", "priority": "NORMAL"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["store_location"])

    def test_order_form_contains_search_and_import_link_only_for_office(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse("new_order"))
        self.assertContains(response, 'id="store-search"')
        self.assertContains(response, "store_lookup.js")
        self.assertNotContains(response, "Import or update store directory")
        self.client.force_login(self.office)
        self.assertContains(
            self.client.get(reverse("new_order")), "Import or update store directory"
        )

    def test_import_upserts_and_repeated_upload_is_noop_without_touching_stock(self):
        with patch(
            "warehouse.store_directory.read_report",
            return_value=[
                ROW | {"address": "UPDATED"},
                ROW | {"store_number": "99999"},
            ],
        ):
            counts = import_report(self.office, b"report-one", "report.xlsx")
            self.assertEqual(counts["created"], 1)
            self.assertEqual(counts["updated"], 1)
            again = import_report(self.office, b"report-one", "report.xlsx")
            self.assertTrue(again["repeated"])
        self.assertEqual(
            Event.objects.filter(action="FD_STORE_DIRECTORY_IMPORT").count(), 1
        )
        self.assertEqual(StoreLocation.objects.count(), 2)
        self.assertFalse(Stock.objects.exists())

    def test_bad_report_does_not_change_directory(self):
        with self.assertRaises(ValidationError):
            import_report(self.office, b"not a workbook", "bad.xlsx")
        self.assertEqual(StoreLocation.objects.count(), 1)
        self.assertFalse(Event.objects.exists())
        with self.assertRaises(PermissionError):
            import_report(self.customer, b"not a workbook", "bad.xlsx")

    def test_later_report_does_not_remove_absent_stores(self):
        with patch(
            "warehouse.store_directory.read_report",
            return_value=[ROW | {"store_number": "99999"}],
        ):
            import_report(self.office, b"new report", "new.xlsx")
        self.store.refresh_from_db()
        self.assertTrue(self.store.active)
