import unittest

from calculator import bounded_percentage, percentage, total


class CalculatorTests(unittest.TestCase):
    def test_totals_values(self) -> None:
        self.assertEqual(total([4, -1, 7]), 10)

    def test_percentage_uses_percent_units(self) -> None:
        self.assertEqual(percentage(1, 4), 25.0)

    def test_bounded_percentage_caps_result(self) -> None:
        self.assertEqual(bounded_percentage(5, 4), 100.0)

    def test_percentage_rejects_zero_whole(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-zero"):
            percentage(1, 0)
