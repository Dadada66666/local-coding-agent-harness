import unittest

from settings import load_feature_state


class SettingsTests(unittest.TestCase):
    def test_settings_support_on_off_and_default(self) -> None:
        self.assertIs(load_feature_state({"feature": "on"}, "feature"), True)
        self.assertIs(load_feature_state({"feature": "off"}, "feature"), False)
        self.assertIs(load_feature_state({}, "feature", default=True), True)
