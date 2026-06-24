import unittest

from enge.utils.source_target_parser import generate_detailed_upgrade_path_alias


class TestDetailedUpgradePathAlias(unittest.TestCase):
    def test_generate_detailed_upgrade_path_alias(self):
        source = {"major": 9, "minor": 8}
        target = {"major": 10, "minor": 2}
        self.assertEqual(
            generate_detailed_upgrade_path_alias(source, target), "98to102"
        )


if __name__ == "__main__":
    unittest.main()
