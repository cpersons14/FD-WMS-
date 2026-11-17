"""Real PostgreSQL requests for the planned ten-user warehouse workload."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from .models import Allocation, Location, Order, Part, Stock
from .services import submit_order
from .tests import DETAILS
from .models import ReceiptGroup
from .receiving import save_receipt, post_receipt
from .backorders import lock_waiting_orders


class TenUserInventoryTests(TransactionTestCase):
    def test_order_arriving_during_receipt_does_not_miss_available_stock(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        actor = get_user_model().objects.create_superuser("receipt-race")
        part = Part.objects.create(sku="RECEIPT-RACE", unit="EA")
        stock = Stock.objects.create(
            part=part,
            location=Location.objects.create(code="RACE-PICK"),
            kind="PICK",
            on_hand=0,
        )
        group = ReceiptGroup.objects.create(
            name="Synthetic", addresses="receipt@example.com"
        )
        receipt = save_receipt(
            actor,
            uuid.uuid4(),
            None,
            0,
            dict(
                supplier="Synthetic",
                received_date=timezone.localdate(),
                reference="RACE",
                group=group,
                notes="",
            ),
            [
                dict(
                    sku=part.sku,
                    location=stock.location.code,
                    quantity="5",
                    unit="EA",
                    loose_eaches="0",
                    case_pack="",
                    pallet_cases="",
                    damaged=False,
                )
            ],
        )

        def new_order():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout='10s'")
                user = get_user_model().objects.get(pk=actor.pk)
                return submit_order(
                    user, DETAILS, [(part.sku, 5, "EA")], uuid.uuid4()
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = None

            def snapshot_then_order(part_ids):
                nonlocal future
                waiting = lock_waiting_orders(part_ids)
                future = pool.submit(new_order)
                # Exercise the gap after selecting waiting orders, before receipt stock is posted.
                # With coordination the new order waits, then sees received stock after commit.
                try:
                    future.result(timeout=1)
                except FutureTimeout:
                    pass
                return waiting

            with patch(
                "warehouse.backorders.lock_waiting_orders",
                side_effect=snapshot_then_order,
            ):
                post_receipt(actor, receipt.pk, receipt.revision)
            order_id = future.result(timeout=10)
        stock.refresh_from_db()
        line = Order.objects.get(pk=order_id).lines.get()
        self.assertEqual((stock.on_hand, stock.reserved, line.backordered), (5, 5, 0))

    def test_ten_simultaneous_orders_conserve_stock_and_backorders(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        group = Group.objects.create(name="FD Corporate")
        users = []
        for number in range(10):
            user = get_user_model().objects.create_user(f"operator-{number}")
            user.groups.add(group)
            users.append(user.pk)
        part = Part.objects.create(sku="TEN-USER-TEST", unit="EA")
        stock = Stock.objects.create(
            part=part,
            location=Location.objects.create(code="TEST-PICK"),
            kind="PICK",
            on_hand=5,
        )
        barrier = threading.Barrier(10)

        def order(user_id):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='10s'")
                    cursor.execute("SET statement_timeout='15s'")
                actor = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=10)
                return submit_order(
                    actor, DETAILS, [(part.sku, 1, "EA")], uuid.uuid4()
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=10) as pool:
            ids = list(pool.map(order, users))
        stock.refresh_from_db()
        self.assertEqual(len(set(ids)), 10)
        self.assertEqual((stock.on_hand, stock.reserved, stock.available), (5, 5, 0))
        self.assertEqual(sum(row.quantity for row in Allocation.objects.all()), 5)
        self.assertEqual(
            sum(
                line.backordered
                for item in Order.objects.all()
                for line in item.lines.all()
            ),
            5,
        )
