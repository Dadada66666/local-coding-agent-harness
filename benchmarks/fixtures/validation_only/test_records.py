import unittest

from records import build_record


class RecordTests(unittest.TestCase):
    def test_builds_normalized_record(self) -> None:
        self.assertEqual(
            build_record(" Release Candidate ", ["High Priority", " Backend "]),
            {
                "label": "Release Candidate",
                "slug": "release-candidate",
                "tags": ["high-priority", "backend"],
            },
        )
