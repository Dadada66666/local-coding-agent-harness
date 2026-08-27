import unittest

from app.models import OrderItem
from app.service import total_cents


class ServiceTests(unittest.TestCase):
    def test_totals_constructed_items(self) -> None:
        items = [OrderItem("PEN", 2, 125), OrderItem("BOOK", 1, 750)]
        self.assertEqual(total_cents(items), 1000)
