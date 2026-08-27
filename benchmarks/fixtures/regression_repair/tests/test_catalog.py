import unittest

from catalog import index_labels


class CatalogTests(unittest.TestCase):
    def test_indexes_labels_by_normalized_key(self) -> None:
        self.assertEqual(
            index_labels([" Release Candidate ", "API_v2-beta"]),
            {
                "release-candidate": "Release Candidate",
                "api_v2-beta": "API_v2-beta",
            },
        )
