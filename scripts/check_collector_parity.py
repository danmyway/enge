#!/usr/bin/env python3
"""Guard: pytest and unittest must discover identical test counts.

A divergence means pytest-style bare-function tests crept in
(unittest silently skips them).

Both collectors run against src/ explicitly. pytest picks it up from
`pythonpath` in pyproject.toml, but `unittest discover` has no equivalent
setting, so without PYTHONPATH it would import an installed enge (e.g. the
COPR RPM) while pytest read the working tree -- comparing two different
codebases and calling it parity.
"""
import os
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _env():
    env = os.environ.copy()
    src = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return env


def pytest_count():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=_env(),
    )
    if result.returncode not in (0, 5):
        print(
            f"FAIL: pytest collection failed (rc={result.returncode}):",
            file=sys.stderr,
        )
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    for line in reversed(result.stdout.strip().splitlines()):
        m = re.search(r"(\d+) tests? collected", line)
        if m:
            return int(m.group(1))

    print("FAIL: could not parse pytest collection count", file=sys.stderr)
    print(result.stdout, file=sys.stderr)
    sys.exit(1)


def unittest_count():
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=_env(),
    )
    if result.returncode != 0:
        print(
            f"FAIL: unittest discover failed (rc={result.returncode}):",
            file=sys.stderr,
        )
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    for line in result.stderr.splitlines():
        m = re.search(r"Ran (\d+) tests? in", line)
        if m:
            return int(m.group(1))

    print("FAIL: could not parse unittest test count", file=sys.stderr)
    print(result.stderr, file=sys.stderr)
    sys.exit(1)


def main():
    pc = pytest_count()
    uc = unittest_count()

    print(f"pytest:   {pc}")
    print(f"unittest: {uc}")

    if pc != uc:
        print(
            f"\nFAIL: collector parity violated ({pc} vs {uc}). "
            "Bare-function tests are invisible to unittest discover.",
        )
        sys.exit(1)

    print("OK: collectors agree")


if __name__ == "__main__":
    main()
