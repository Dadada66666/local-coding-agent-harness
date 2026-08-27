import unittest

from pricing import LineItem, apply_discount, order_total, subtotal


class PricingTests(unittest.TestCase):
    def test_subtotal_uses_quantity(self) -> None:
        self.assertEqual(subtotal([LineItem(1250, 2), LineItem(500, 1)]), 3000)

    def test_applies_percentage_discount(self) -> None:
        self.assertEqual(apply_discount(5000, 10), 4500)

    def test_order_total_without_discount(self) -> None:
        self.assertEqual(order_total([LineItem(999, 2)]), 1998)

    def test_rejects_invalid_discount(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            apply_discount(1000, 101)
