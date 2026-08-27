import unittest

from strings import normalize_label


class StringTests(unittest.TestCase):
    def test_normalizes_spacing_and_case(self) -> None:
        self.assertEqual(normalize_label("  Hello   World "), "hello-world")

    def test_preserves_single_word(self) -> None:
        self.assertEqual(normalize_label("Agent"), "agent")

    def test_normalizes_tabs(self) -> None:
        self.assertEqual(normalize_label("Hello\tWorld"), "hello-world")
