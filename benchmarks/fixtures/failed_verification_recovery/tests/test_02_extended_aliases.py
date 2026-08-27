import unittest

from flags import parse_bool


class ExtendedAliasTests(unittest.TestCase):
    def test_enabled_and_disabled_aliases(self) -> None:
        self.assertIs(parse_bool("enabled"), True)
        self.assertIs(parse_bool("disabled"), False)
