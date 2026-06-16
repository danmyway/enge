import unittest
from unittest.mock import MagicMock
from enge.utils.source_target_parser import resolve_effective_values


class TestSingleResolution(unittest.TestCase):
    def test_tier_fallback_info_fires_once(self):
        """When tiers come from [tests].tiers fallback, the INFO line fires exactly once."""
        cli_args = MagicMock()
        cli_args.tier = None
        cli_args.source = None
        cli_args.target = None
        cli_args.architectures = None
        cli_args.pool = None
        cli_args.git_ref = "main"
        cli_args.git_url = None
        cli_args.parallel_limit = None
        cli_args.event = None
        cli_args.plan = None

        config = {
            "tests": {
                "tiers": ["tier3"],
                "tier": {"tier3": "tag:tier[0123]"},
            }
        }
        set_config = {}  # no tiers in set -> falls back to [tests].tiers

        with self.assertLogs("enge.utils.source_target_parser", level="INFO") as cm:
            resolve_effective_values(cli_args, set_config, config)

        tier_msgs = [m for m in cm.output if "tiers not specified" in m]
        self.assertEqual(
            len(tier_msgs),
            1,
            f"Expected exactly 1 tier-fallback INFO, got {len(tier_msgs)}: {tier_msgs}",
        )


if __name__ == "__main__":
    unittest.main()
