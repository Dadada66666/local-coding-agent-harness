import unittest

from app.formatter import format_cents
from app.parser import parse_order_line
from app.service import total_cents


class IntegrationTests(unittest.TestCase):
    def test_parsed_order_total(self) -> None:
        items = [parse_order_line("PEN, 2, 1.25"), parse_order_line("BOOK, 1, 7.50")]
        self.assertEqual(format_cents(total_cents(items)), "$10.00")

    def test_parsed_fractional_price(self) -> None:
        self.assertEqual(parse_order_line("CARD, 1, 0.99").unit_price_cents, 99)
