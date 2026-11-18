import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from .models import (
    Allocation,
    DocumentSequence,
    Location,
    Order,
    Part,
    Shipment,
    Stock,
)
from .operations import fulfill, set_hold
from .requisitions import match_reference
from .services import WorkflowError, submit_order, transfer_stock
from .tests import DETAILS


@override_settings(AXES_ENABLED=False)
class RequisitionTests(TestCase):
    def setUp(self):
        self.wh = get_user_model().objects.create_superuser("numbering-office")
        self.fd = get_user_model().objects.create_user("numbering-fd")
        self.fd.groups.add(Group.objects.get_or_create(name="FD Corporate")[0])
        self.part = Part.objects.create(sku="NUMBERED", unit="EA")
        self.stock = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="REQ-PICK"),
            kind="PICK",
            on_hand=10,
        )

    def order(self, qty=10, key=None):
        return submit_order(
            self.fd, DETAILS, [(self.part.sku, qty)], key or uuid.uuid4()
        )

    def act(self, req, action, **kwargs):
        return fulfill(self.wh, req.order_id, action, requisition_id=req.pk, **kwargs)

    def pack(self, req, qty):
        self.act(
            req,
            "pack",
            line_id=req.order.lines.get().pk,
            amount=qty,
            scan=self.part.sku,
        )

    def release_stock(self, qty):
        held = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code=f"HELD-{uuid.uuid4().hex[:8]}"),
            kind="PICK",
            on_hand=qty,
            held=True,
        )
        set_hold(self.wh, held.pk, False, "Synthetic release")
        return held

    def test_new_document_and_shipment_generated_and_retry_preserved(self):
        key = uuid.uuid4()
        order = self.order(key=key)
        retry = self.order(key=key)
        req = order.requisitions.get()
        self.assertEqual(order.pk, retry.pk)
        self.assertEqual(order.number, "FD000001")
        self.assertEqual(req.reference, "FD000001 001")
        self.assertEqual(req.lines.get().quantity, 10)
        self.assertEqual(order.reference, "Pilot")
        self.assertEqual(order.next_shipment_number, 2)
        self.assertEqual(DocumentSequence.objects.get().value, 1)
        self.assertEqual(Allocation.objects.get().requisition_id, req.pk)
        self.assertEqual(self.order().number, "FD000002")

    def test_customer_cannot_choose_document_number(self):
        with self.assertRaises(WorkflowError):
            submit_order(
                self.fd,
                {**DETAILS, "document_number": "FD999999"},
                [(self.part.sku, 1)],
                uuid.uuid4(),
            )
        self.assertFalse(Order.objects.exists())

    def test_failed_submission_rolls_back_number_and_stock(self):
        with self.assertRaises(WorkflowError):
            submit_order(
                self.wh,
                {**DETAILS, "allow_backorder": False},
                [(self.part.sku, 20)],
                uuid.uuid4(),
            )
        self.assertEqual(self.order().number, "FD000001")

    def test_backorders_share_document_but_not_shipment_or_quantities(self):
        order = self.order(15)
        first = order.requisitions.get()
        self.act(first, "pick")
        self.pack(first, 10)
        self.act(first, "ship", carrier="UPS", tracking="TEST-UPS")
        self.release_stock(5)
        second = order.requisitions.get(shipment_number=2)
        self.assertEqual(second.reference, f"{order.number} 002")
        self.assertEqual(second.lines.get().quantity, 5)
        self.assertEqual(order.pick_releases.get().number, second.reference)
        self.act(second, "pick")
        self.pack(second, 5)
        self.act(second, "ship", carrier="LTL", tracking="TEST-PRO")
        self.assertEqual(first.shipment.tracking, "TEST-UPS")
        self.assertEqual(second.shipment.tracking, "TEST-PRO")
        self.assertEqual(order.lines.get().shipped, 15)

    def test_entirely_backordered_first_receipt_uses_reserved_initial_number(self):
        self.stock.on_hand = 0
        self.stock.save()
        order = self.order(5)
        initial = order.requisitions.get()
        self.assertEqual(initial.status, "Awaiting stock")
        self.release_stock(3)
        self.assertEqual(order.requisitions.count(), 1)
        self.assertEqual(initial.lines.get().quantity, 3)
        self.release_stock(2)
        self.assertEqual(
            order.requisitions.get(shipment_number=2).lines.get().quantity, 2
        )

    def test_simultaneous_open_releases_require_explicit_selection(self):
        order = self.order(15)
        self.release_stock(5)
        with self.assertRaisesMessage(WorkflowError, "Choose the requisition"):
            fulfill(self.wh, order.pk, "pick")
        first, second = list(order.requisitions.all())
        self.act(second, "pick")
        self.assertEqual(first.lines.get().picked, 0)
        self.assertEqual(second.lines.get().picked, 5)
        with self.assertRaises(WorkflowError):
            self.pack(first, 1)
        self.pack(second, 5)
        self.act(second, "ship", carrier="UPS", tracking="SECOND-FIRST")
        self.assertEqual(first.lines.get().shipped, 0)

    def test_partial_dispatch_moves_reserved_and_unpacked_picks_to_new_reference(self):
        order = self.order()
        first = order.requisitions.get()
        line = order.lines.get()
        self.act(first, "pick", line_id=line.pk, amount=7)
        self.pack(first, 4)
        key = uuid.uuid4()
        for _ in range(2):
            self.act(first, "ship", carrier="UPS", tracking="PARTIAL", key=key)
        second = order.requisitions.get(shipment_number=2)
        self.assertEqual(
            (first.lines.get().quantity, first.lines.get().shipped), (4, 4)
        )
        row = second.lines.get()
        self.assertEqual(
            (row.quantity, row.picked, row.packed, row.reserved), (6, 3, 0, 3)
        )
        self.assertEqual(sum(a.quantity for a in second.allocations.all()), 6)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.on_hand, self.stock.reserved), (3, 3))
        self.act(second, "unpick", line_id=line.pk, amount=3, note="Correction")
        self.act(second, "pick")
        self.pack(second, 6)
        self.act(second, "ship", carrier="UPS", tracking="REMAINDER")
        self.assertEqual(order.lines.get().shipped, 10)
        self.assertEqual(order.shipments.count(), 2)

    def test_shipping_failure_rolls_back_split_number_and_quantities(self):
        order = self.order()
        first = order.requisitions.get()
        self.act(first, "pick")
        self.pack(first, 4)
        with patch(
            "warehouse.operations.Shipment.objects.create",
            side_effect=RuntimeError("Synthetic failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.act(first, "ship", carrier="UPS", tracking="FAIL")
        order.refresh_from_db()
        self.assertEqual(order.next_shipment_number, 2)
        self.assertEqual(order.requisitions.count(), 1)
        self.assertEqual(first.lines.get().quantity, 10)
        self.assertEqual(first.allocations.get().quantity, 10)

    def test_cross_document_or_malformed_selection_does_not_change_stock(self):
        first = self.order(2)
        other = self.order(2)
        for bad in [other.requisitions.get().pk, "bad-id"]:
            with self.assertRaises(WorkflowError):
                fulfill(self.wh, first.pk, "pick", requisition_id=bad)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.on_hand, self.stock.reserved), (10, 4))

    def test_one_tracking_record_enforced_in_database(self):
        order = self.order()
        req = order.requisitions.get()
        self.act(req, "pick")
        self.pack(req, 10)
        self.act(req, "ship", carrier="UPS", tracking="ONE")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Shipment.objects.create(
                order=order,
                requisition=req,
                actor=self.wh,
                carrier="UPS",
                tracking="TWO",
                request_key=uuid.uuid4(),
            )

    def test_transfer_preserves_requisition_and_pick_quantities(self):
        order = self.order()
        req = order.requisitions.get()
        target = Stock.objects.create(
            part=self.part,
            location=Location.objects.create(code="OTHER-PICK"),
            kind="PICK",
            on_hand=0,
        )
        transfer_stock(self.wh, self.stock.pk, target.pk, 4, "Move reservation")
        self.assertEqual(
            set(order.lines.get().allocations.values_list("requisition_id", flat=True)),
            {req.pk},
        )
        self.act(req, "pick")
        self.assertEqual(req.lines.get().picked, 10)

    def test_exact_reference_matching_no_document_only_guess(self):
        order = self.order(15)
        self.release_stock(5)
        req = order.requisitions.get(shipment_number=2)
        self.assertEqual(match_reference(f"  {order.number.lower()}   002 ").pk, req.pk)
        for ref in [order.number, f"{order.number}002", f"{order.number} 099", "Pilot"]:
            with self.assertRaises(WorkflowError):
                match_reference(ref)

    def test_selected_picklist_search_and_customer_permissions(self):
        order = self.order(15)
        self.release_stock(5)
        req = order.requisitions.get(shipment_number=2)
        other = self.order(1)
        self.client.force_login(self.fd)
        detail = self.client.get(f"/orders/{order.pk}/?requisition={req.pk}")
        self.assertContains(detail, req.reference)
        self.assertNotContains(detail, "Confirm shipment")
        for search in [order.number, req.reference, order.legacy_number]:
            response = self.client.get("/orders/", {"q": search})
            self.assertContains(response, order.number)
            self.assertNotContains(response, other.number)
        printed = self.client.get(f"/orders/{order.pk}/print/", {"requisition": req.pk})
        self.assertContains(printed, req.reference)
        self.assertNotContains(printed, "REQ-PICK")
        bad = self.client.get(
            f"/orders/{order.pk}/print/", {"requisition": other.requisitions.get().pk}
        )
        self.assertEqual(bad.status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/orders/{order.pk}/action/",
                {"action": "pick", "requisition": req.pk, "request_key": uuid.uuid4()},
            ).status_code,
            403,
        )
        self.client.force_login(self.wh)
        response = self.client.get(f"/orders/{order.pk}/", {"requisition": req.pk})
        self.assertContains(response, f'name="requisition" value="{req.pk}"')
        response = self.client.post(
            f"/orders/{order.pk}/action/",
            {"action": "pick", "requisition": req.pk, "request_key": uuid.uuid4()},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(req.pk), response.url)


class ConcurrentNumberingTests(TransactionTestCase):
    def test_ten_orders_for_different_parts_generate_unique_documents(self):
        actor = get_user_model().objects.create_superuser("concurrent-numbering")
        skus = [f"PARALLEL-{n}" for n in range(10)]
        Part.objects.bulk_create([Part(sku=sku, unit="EA") for sku in skus])
        barrier = threading.Barrier(10)

        def create(sku):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout='10s'")
                user = get_user_model().objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                order = submit_order(user, DETAILS, [(sku, 1)], uuid.uuid4())
                return order.number, order.requisitions.get().reference
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(create, skus))
        self.assertEqual(
            {doc for doc, _ in results}, {f"FD{n:06d}" for n in range(1, 11)}
        )
        self.assertEqual(len({ref for _, ref in results}), 10)


class NumberingMigrationTests(TransactionTestCase):
    def test_existing_partial_shipment_and_open_work_preserve_balances(self):
        old = ("warehouse", "0015_backstock_references_order_increments")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes("warehouse")[0]
        executor.migrate([old])
        apps = executor.loader.project_state([old]).apps
        try:

            def model(name):
                return apps.get_model("warehouse", name)

            user = apps.get_model("auth", "User").objects.create(
                username="legacy-numbering"
            )
            part = model("Part").objects.create(sku="LEGACY-REQ", unit="EA")
            loc = model("Location").objects.create(code="LEGACY-BIN")
            stock = model("Stock").objects.create(
                part=part, location=loc, kind="PICK", on_hand=20, reserved=3
            )
            order = model("Order").objects.create(
                created_by=user, request_key=uuid.uuid4(), status="PARTIAL", **DETAILS
            )
            line = model("OrderLine").objects.create(
                order=order, part=part, quantity=15, picked=7, packed=6, shipped=4
            )
            model("Allocation").objects.create(
                line=line, stock=stock, quantity=7, state="PICKED"
            )
            model("Allocation").objects.create(
                line=line, stock=stock, quantity=3, state="RESERVED"
            )
            ship = model("Shipment").objects.create(
                order=order,
                actor=user,
                request_key=uuid.uuid4(),
                carrier="UPS",
                tracking="KEEP-THIS",
            )
            model("ShipmentLine").objects.create(shipment=ship, line=line, quantity=4)
        finally:
            MigrationExecutor(connection).migrate([latest])
        migrated = Order.objects.get(pk=order.pk)
        line = migrated.lines.get()
        self.assertEqual(
            (line.quantity, line.picked, line.packed, line.shipped, line.backordered),
            (15, 7, 6, 4, 5),
        )
        self.assertEqual(migrated.shipments.get().tracking, "KEEP-THIS")
        first, second = list(migrated.requisitions.all())
        self.assertEqual(first.lines.get().shipped, 4)
        row = second.lines.get()
        self.assertEqual(
            (row.quantity, row.picked, row.packed, row.reserved), (6, 3, 2, 3)
        )
        self.assertEqual(sum(a.quantity for a in second.allocations.all()), Decimal(6))
        self.assertEqual(
            line.allocations.filter(requisition__isnull=True).get().quantity, 4
        )
        stock = Stock.objects.get(pk=stock.pk)
        self.assertEqual((stock.on_hand, stock.reserved), (20, 3))
        self.assertEqual(migrated.next_shipment_number, 3)
        self.assertEqual(DocumentSequence.objects.get().value, 1)
