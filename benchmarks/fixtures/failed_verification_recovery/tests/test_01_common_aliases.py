import unittest

from flags import parse_bool


class CommonAliasTests(unittest.TestCase):
    def test_yes_and_no_aliases(self) -> None:
        self.assertIs(parse_bool(" yes "), True)
        self.assertIs(parse_bool("NO"), False)
