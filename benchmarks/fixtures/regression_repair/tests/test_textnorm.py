import unittest

from textnorm import normalize_key


class TextNormalizationTests(unittest.TestCase):
    def test_normalizes_simple_space(self) -> None:
        self.assertEqual(normalize_key("Blue Sky"), "blue-sky")

    def test_preserves_existing_separators(self) -> None:
        self.assertEqual(normalize_key(" API_v2-beta "), "api_v2-beta")

    def test_collapses_mixed_whitespace(self) -> None:
        self.assertEqual(normalize_key("  Blue\t  Sky  "), "blue-sky")
