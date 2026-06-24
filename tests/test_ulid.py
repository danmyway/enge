import time
import unittest
from datetime import datetime, timezone

from enge.utils.ulid import CROCKFORD, generate_ulid, ulid_timestamp


class TestULID(unittest.TestCase):
    def test_length_is_26(self):
        self.assertEqual(len(generate_ulid()), 26)

    def test_uses_only_crockford_alphabet(self):
        ulid = generate_ulid()
        allowed = set(CROCKFORD)
        for ch in ulid:
            self.assertIn(ch, allowed, f"unexpected char {ch!r} in {ulid}")

    def test_lexical_sort_matches_creation_order(self):
        first = generate_ulid()
        time.sleep(0.002)
        second = generate_ulid()
        self.assertLess(first, second)

    def test_timestamp_round_trip(self):
        before = datetime.now(timezone.utc)
        ulid = generate_ulid()
        after = datetime.now(timezone.utc)
        ts = ulid_timestamp(ulid)
        self.assertGreaterEqual(ts, before.replace(microsecond=0))
        self.assertLessEqual(
            ts.timestamp(),
            after.timestamp() + 1,
        )


if __name__ == "__main__":
    unittest.main()
