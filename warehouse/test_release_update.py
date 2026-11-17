import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from .models import (
    Part,
    Stock,
    Location,
    Event,
    ReceiptGroup,
    ReceiptEmail,
    PickRelease,
    PurchaseOrder,
    Allocation,
)
from .tests import DETAILS
from .test_receiving import SETTINGS
from .services import submit_order, transition_order, transfer_stock, WorkflowError
from .receiving import save_receipt, post_receipt
from .backorders import change_priority
from .operations import set_hold
from .reporting import stock_summary, due_counts
from .purchase_orders import (
    import_purchase_orders,
    progress,
    correct_accepted,
    correct_expected,
    correct_receipt_po,
)
from .icc_locations import correct_locations, approved_bin, IMPORT_ID


@override_settings(**SETTINGS)
class ReleaseUpdateTests(TestCase):
    def setUp(self):
        self.wh = get_user_model().objects.create_superuser("office")
        self.fd = get_user_model().objects.create_user("fd")
        self.fd.groups.add(Group.objects.get_or_create(name="FD Corporate")[0])
        self.part = Part.objects.create(sku="001", unit="EA", units_per_case=12)
        self.stock = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="P1"),
            kind="PICK",
            on_hand=0,
        )
        self.group = ReceiptGroup.objects.create(
            name="FD", addresses="receipts@example.com"
        )

    def order(self, qty=20):
        return submit_order(
            self.fd, DETAILS, [(self.part.sku, qty, "EA")], uuid.uuid4()
        )

    def draft(self, qty=15, stock=None, po="000123"):
        stock = stock or self.stock
        row = dict(
            sku=stock.part.sku,
            location=stock.location.code,
            quantity=str(qty),
            unit="EA",
            loose_eaches="0",
            case_pack=str(stock.part.units_per_case or ""),
            pallet_cases="",
            note="",
            damaged=stock.held,
        )
        return save_receipt(
            self.wh,
            uuid.uuid4(),
            None,
            0,
            dict(
                supplier="Vendor",
                received_date=timezone.localdate(),
                reference="BOL-1",
                po_number=po,
                group=self.group,
                notes="",
            ),
            [row],
        )

    def receipt(self, *args, **kwargs):
        r = self.draft(*args, **kwargs)
        return post_receipt(self.wh, r.pk, r.revision)

    def po(self, rows="000123,Vendor,001,20\n"):
        data = ("PO Number,Vendor,Part Number,Expected EA\n" + rows).encode()
        return import_purchase_orders(self.wh, data, "po.csv")

    def test_partial_ship_then_two_receipts_release_only_remaining_once(self):
        self.stock.on_hand = 5
        self.stock.save()
        order = self.order(25)
        line = order.lines.get()
        transition_order(self.wh, order.pk, "pick")
        summary = stock_summary()[0]
        self.assertEqual(
            (
                summary["on_hand"],
                summary["allocated"],
                summary["available"],
                summary["staged"],
            ),
            (5, 5, 0, 5),
        )
        transition_order(
            self.wh, order.pk, "pack", line_id=line.pk, amount=5, unit="EA", scan="001"
        )
        transition_order(self.wh, order.pk, "ship", carrier="UPS", tracking="TEST")
        receipt = self.receipt(15)
        post_receipt(self.wh, receipt.pk, receipt.revision)
        line.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(
            (line.shipped, line.reserved_quantity, line.backordered), (5, 15, 5)
        )
        self.assertEqual((self.stock.on_hand, self.stock.reserved), (15, 15))
        self.assertEqual(PickRelease.objects.count(), 1)
        self.assertEqual(ReceiptEmail.objects.count(), 1)
        self.assertEqual(receipt.pick_releases.get().order, order)
        self.receipt(12)
        line.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(
            (line.reserved_quantity, line.backordered, self.stock.available), (20, 0, 7)
        )
        self.assertEqual(PickRelease.objects.count(), 2)

    def test_fifo_and_audited_urgent_override(self):
        first = self.order(10)
        second = self.order(10)
        urgent = self.order(10)
        change_priority(self.wh, urgent.pk, "URGENT", "Store opening")
        self.receipt(15)
        self.assertEqual(urgent.lines.get().reserved_quantity, 10)
        self.assertEqual(first.lines.get().reserved_quantity, 5)
        self.assertEqual(second.lines.get().reserved_quantity, 0)
        self.assertEqual(Event.objects.filter(action="ORDER_PRIORITY").count(), 1)

    def test_customer_cannot_raise_priority_or_use_cases(self):
        with self.assertRaises(WorkflowError):
            submit_order(self.fd, DETAILS, [("001", 1, "CASE")], uuid.uuid4())
        order = submit_order(
            self.fd,
            {**DETAILS, "priority": "URGENT", "allow_backorder": False},
            [("001", 3, "EA")],
            uuid.uuid4(),
        )
        self.assertEqual(order.priority, "NORMAL")
        self.assertTrue(order.allow_backorder)
        with self.assertRaises(PermissionError):
            change_priority(self.fd, order.pk, "URGENT", "test")
        self.client.force_login(self.fd)
        self.assertNotContains(self.client.get("/orders/new/"), 'value="CASE"')
        self.assertEqual(
            self.client.post(
                f"/orders/{order.pk}/priority/", {"priority": "URGENT", "note": "test"}
            ).status_code,
            403,
        )

    def test_held_and_cancelled_do_not_allocate_then_inspection_release_does(self):
        cancelled = self.order()
        transition_order(self.wh, cancelled.pk, "cancel")
        waiting = self.order()
        quarantine = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="Q"),
            kind="OVERSTOCK",
            on_hand=0,
            held=True,
        )
        receipt = self.receipt(15, stock=quarantine)
        self.assertEqual(receipt.lines.get().accepted_quantity, 0)
        self.assertFalse(PickRelease.objects.exists())
        with self.assertRaises(WorkflowError):
            correct_accepted(self.wh, receipt.lines.get().pk, 15, "Inspected")
        set_hold(self.wh, quarantine.pk, False, "Inspected all goods")
        self.assertEqual(waiting.lines.get().reserved_quantity, 15)
        self.assertEqual(cancelled.lines.get().reserved_quantity, 0)
        correct_accepted(
            self.wh, receipt.lines.get().pk, 15, "Accepted after inspection"
        )
        self.assertEqual(receipt.lines.get().accepted_quantity, 15)

    def test_bad_receipt_rolls_back_all_stock_and_releases(self):
        self.order()
        receipt = self.draft()
        receipt.draft_lines.append({**receipt.draft_lines[0], "sku": "MISSING"})
        receipt.save()
        with self.assertRaises(WorkflowError):
            post_receipt(self.wh, receipt.pk, receipt.revision)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.on_hand, 0)
        self.assertFalse(PickRelease.objects.exists())
        self.assertFalse(ReceiptEmail.objects.exists())

    def test_release_tracks_transfer_partial_pick_and_unpick(self):
        order = self.order()
        self.receipt()
        release = PickRelease.objects.get()
        target = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="P2"),
            kind="PICK",
            on_hand=0,
        )
        transfer_stock(self.wh, self.stock.pk, target.pk, 6, "Replenish", unit="EA")
        self.assertEqual(sum(a.quantity for a in release.allocations.all()), 15)
        line = order.lines.get()
        transition_order(
            self.wh, order.pk, "pick", line_id=line.pk, amount=3, unit="EA"
        )
        self.assertEqual(
            sum(a.quantity for a in release.allocations.filter(state="PICKED")), 3
        )
        transition_order(
            self.wh,
            order.pk,
            "unpick",
            line_id=line.pk,
            amount=1,
            unit="EA",
            note="Correction",
        )
        self.assertEqual(
            sum(a.quantity for a in release.allocations.filter(state="PICKED")), 2
        )

    def test_po_per_item_progress_not_aggregate_and_reopens_on_correction(self):
        second = Part.objects.create(sku="002", unit="EA")
        stock2 = Stock.objects.create(
            part=second, location=self.stock.location, kind="PICK", on_hand=0
        )
        self.po("000123,Vendor,001,20\n000123,Vendor,002,10\n")
        po = PurchaseOrder.objects.get()
        first = self.receipt(30)
        self.assertFalse(progress(po)["complete"])
        self.assertEqual(progress(po)["percent"], Decimal(200) / 3)
        self.receipt(10, stock=stock2)
        self.assertTrue(progress(po)["complete"])
        correct_accepted(
            self.wh, first.lines.get().pk, 19, "One unit rejected on review"
        )
        self.assertFalse(progress(po)["complete"])
        correct_expected(
            self.wh, po.lines.get(part=self.part).pk, 19, "Approved PO amendment"
        )
        self.assertTrue(progress(po)["complete"])

    def test_po_import_repeat_no_overwrite_and_error_is_atomic(self):
        self.po()
        result = self.po()
        self.assertTrue(result["unchanged"])
        with self.assertRaises(WorkflowError):
            self.po("000123,Vendor,001,25\n")
        with self.assertRaises(WorkflowError):
            self.po("000999,Vendor,001,5\n000123,Vendor,001,99\n")
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.get().lines.get().expected_quantity, 20)

    def test_one_delivery_can_fill_multiple_pos_with_one_email(self):
        self.po("000123,Vendor,001,15\n000456,Vendor,001,5\n")
        receipt = self.draft(15)
        receipt.draft_lines.append(
            {**receipt.draft_lines[0], "quantity": "5", "line_po": "000456"}
        )
        receipt.save()
        post_receipt(self.wh, receipt.pk, receipt.revision)
        self.assertEqual(ReceiptEmail.objects.count(), 1)
        for po in PurchaseOrder.objects.all():
            self.assertTrue(progress(po)["complete"])
        corrected = receipt.lines.get(po_number="000456")
        correct_accepted(self.wh, corrected.pk, 5, "PO number correction", "000789")
        self.assertFalse(
            progress(PurchaseOrder.objects.get(number="000456"))["complete"]
        )

    def test_po_import_after_receipt_and_reference_correction(self):
        receipt = self.receipt(20, po="")
        self.po()
        po = PurchaseOrder.objects.get()
        self.assertFalse(progress(po)["complete"])
        correct_receipt_po(self.wh, receipt.pk, "000123", "PO located on BOL")
        self.assertTrue(progress(po)["complete"])
        self.assertEqual(ReceiptEmail.objects.count(), 1)
        self.client.force_login(self.wh)
        self.assertContains(
            self.client.get("/receipts/?vendor=Vendor&po=000123"), receipt.number
        )

    def test_office_pages_and_customer_restrictions(self):
        self.order()
        receipt = self.receipt()
        self.po()
        release = PickRelease.objects.get()
        po = PurchaseOrder.objects.get()
        urls = [
            "/work/",
            f"/releases/{release.pk}/",
            "/purchase-orders/",
            f"/purchase-orders/{po.pk}/",
            "/purchase-orders/template/",
            f"/receipts/{receipt.pk}/",
        ]
        self.client.force_login(self.wh)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.force_login(self.fd)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)


@override_settings(**SETTINGS)
class BinCorrectionTests(TestCase):
    def setUp(self):
        self.wh = get_user_model().objects.create_superuser("office")

    def make(
        self,
        sku="001",
        bins=("MST BX", "H20 H24"),
        stock_rows=(("ICC-TEST-UNASSIGNED", 8), ("H20 H24", 0)),
    ):
        part = Part.objects.create(sku=sku, unit="EA")
        source = {"C": sku, "L": bins[0], "O": bins[1], "I": "8", "M": "999"}
        for code, qty in stock_rows:
            location, _ = Location.objects.get_or_create(code=code)
            stock = Stock.objects.create(
                part=part, location=location, kind="PICK", on_hand=qty
            )
            Event.objects.create(
                actor=self.wh,
                stock=stock,
                action="ICC_TEST_IMPORT",
                after=qty,
                quantity=qty,
                entry={"import_id": IMPORT_ID, "source": source},
            )
        return part

    def test_rules_keep_literals_and_do_not_split_codes(self):
        for source, wanted in [
            ({"L": "MST BX", "O": "H20 H24"}, "H20 H24"),
            ({"L": "TBD", "O": "J57"}, "J57"),
            ({"L": "TBD"}, "TBD"),
            ({"L": "MST BX"}, "MST BX"),
            ({"L": "J57", "O": "J57"}, "J57"),
            ({"L": "P1", "O": "P2"}, None),
            ({}, None),
        ]:
            self.assertEqual(approved_bin(source), wanted)

    def test_safe_consolidation_preserves_qty_audit_and_no_count(self):
        part = self.make()
        self.assertEqual(correct_locations(self.wh)[0]["status"], "READY")
        self.assertFalse(Stock.objects.filter(archived=True).exists())
        self.assertEqual(correct_locations(self.wh, True)[0]["status"], "APPLY")
        active = Stock.objects.get(part=part, archived=False)
        self.assertEqual((active.location.code, active.on_hand), ("H20 H24", 8))
        self.assertIsNone(active.last_counted_at)
        self.assertEqual(sum(s.on_hand for s in Stock.objects.filter(part=part)), 8)
        self.assertEqual(Event.objects.filter(action="ICC_TEST_IMPORT").count(), 2)
        self.assertEqual(correct_locations(self.wh, True)[0]["status"], "UNCHANGED")
        self.assertFalse(any(s.archived for s in due_counts()))

    def test_activity_blocks_correction(self):
        part = self.make()
        stock = part.stock_set.first()
        Event.objects.create(
            actor=self.wh, stock=stock, action="COUNT", note="Activity after import"
        )
        self.assertEqual(correct_locations(self.wh, True)[0]["status"], "REVIEW")
        self.assertFalse(Stock.objects.filter(archived=True).exists())


class ReleaseConcurrencyTests(TransactionTestCase):
    def test_overlapping_receipts_do_not_double_allocate_or_deadlock(self):
        from django.db import connection, close_old_connections

        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        import threading
        from concurrent.futures import ThreadPoolExecutor

        actor = get_user_model().objects.create_superuser("office")
        part = Part.objects.create(sku="001", unit="EA")
        stock = Stock.objects.create(
            part=part,
            location=Location.objects.create(code="P1"),
            kind="PICK",
            on_hand=0,
        )
        group = ReceiptGroup.objects.create(name="FD", addresses="receipts@example.com")
        order = submit_order(
            actor,
            {**DETAILS, "allow_backorder": True},
            [("001", 20, "EA")],
            uuid.uuid4(),
        )
        receipts = []
        for _ in range(2):
            receipts.append(
                save_receipt(
                    actor,
                    uuid.uuid4(),
                    None,
                    0,
                    dict(
                        supplier="V",
                        received_date=timezone.localdate(),
                        reference="B",
                        group=group,
                        notes="",
                    ),
                    [
                        dict(
                            sku="001",
                            location="P1",
                            quantity="15",
                            unit="EA",
                            loose_eaches="0",
                            case_pack="",
                            pallet_cases="",
                            damaged=False,
                        )
                    ],
                )
            )
        # Separate recipient groups avoid serializing both receipts on the same group row.
        receipts[1].group = ReceiptGroup.objects.create(
            name="Second group", addresses="second@example.com"
        )
        receipts[1].save(update_fields=["group"])
        barrier = threading.Barrier(2)

        def post(receipt):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='5s'")
                user = get_user_model().objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                post_receipt(user, receipt.pk, receipt.revision)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(post, receipts))
        stock.refresh_from_db()
        self.assertEqual((stock.on_hand, stock.reserved), (30, 20))
        self.assertEqual(order.lines.get().backordered, 0)
        self.assertEqual(sum(a.quantity for a in Allocation.objects.all()), 20)
