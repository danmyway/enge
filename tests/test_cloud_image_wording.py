"""User-visible wording of cloud-image source messages (L42, Q-RN-1).

Alma, Rocky *and* Oracle (Azure URNs) are cloud-image sources, so the two
error messages and two DEBUG lines in ``source_target_parser`` must not call
them "AMI". Each test asserts the new text first and the absence of "AMI"
second, so the negative cannot pass vacuously.
"""

import logging
import unittest

from enge.utils.errors import ValidationError
from enge.utils.source_target_parser import (
    parse_compose_spec,
    validate_cloud_image_architectures,
)
from tests._helpers import captured_logs, matching

LOGGER_NAME = "enge.utils.source_target_parser"

ORACLE_URN = "Oracle:Oracle-Linux:ol98-lvm-gen2:9.8.2"
CONFIG = {
    "testing_farm": {"composes_prod_url": ""},
    "sources": {
        "images": {
            "alma97": "AlmaLinux OS 9.7.20251118",
            "rocky97": "Rocky-9-EC2-Base-9.7-20251123.2",
            "oracle9": ORACLE_URN,
            "bad_alias": "NotAnImageName",
        }
    },
}


class TestCloudImageWording(unittest.TestCase):
    def test_unmatched_alias_error_says_cloud_image(self):
        with self.assertRaises(ValueError) as cm:
            parse_compose_spec("bad_alias", CONFIG)
        message = str(cm.exception)
        self.assertIn(
            "Cloud image alias 'bad_alias' resolved to 'NotAnImageName'", message
        )
        self.assertIn("cloud image name pattern", message)
        self.assertNotIn("AMI", message)

    def test_unsupported_arch_error_says_cloud_image(self):
        spec = parse_compose_spec("oracle9", CONFIG)
        with self.assertRaises(ValidationError) as cm:
            validate_cloud_image_architectures(spec, ["aarch64"])
        message = str(cm.exception)
        self.assertIn("Oracle Linux cloud image sources", message)
        self.assertNotIn("AMI", message)

    def test_alias_resolution_debug_line_says_cloud_image(self):
        with captured_logs(LOGGER_NAME) as records:
            parse_compose_spec("alma97", CONFIG)
        self.assertEqual(
            len(
                matching(
                    records,
                    "Resolved cloud image alias 'alma97'",
                    level=logging.DEBUG,
                )
            ),
            1,
        )
        self.assertEqual(
            [r.getMessage() for r in records if "AMI" in r.getMessage()], []
        )

    def test_parsed_spec_debug_line_says_cloud_image(self):
        with captured_logs(LOGGER_NAME) as records:
            parse_compose_spec("rocky97", CONFIG)
        self.assertEqual(
            len(matching(records, "Linux cloud image spec", level=logging.DEBUG)),
            1,
        )
        self.assertEqual(
            [r.getMessage() for r in records if "AMI" in r.getMessage()], []
        )


if __name__ == "__main__":
    unittest.main()
