import os
import time
from datetime import datetime, timezone

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def generate_ulid() -> str:
    ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ts_ms, 10) + _encode(rand, 16)


def ulid_timestamp(ulid: str) -> datetime:
    ts_ms = 0
    for ch in ulid[:10]:
        ts_ms = (ts_ms << 5) | CROCKFORD.index(ch.upper())
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
