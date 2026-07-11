"""
Drift guard: docs/cli.md must stay byte-identical to the generator's output.
"""

import importlib.util
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_cli_docs.py"
DOCS_PATH = REPO_ROOT / "docs" / "cli.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_cli_docs", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCliDocsDrift(unittest.TestCase):
    def test_docs_cli_md_matches_generator_output(self):
        generator = _load_generator()
        generated = generator.generate_markdown()

        self.assertTrue(
            DOCS_PATH.exists(),
            f"{DOCS_PATH} is missing. Generate it with: "
            "python scripts/generate_cli_docs.py --write",
        )
        actual = DOCS_PATH.read_text()
        self.assertEqual(
            actual,
            generated,
            f"{DOCS_PATH} is stale. Regenerate with: "
            "python scripts/generate_cli_docs.py --write",
        )


if __name__ == "__main__":
    unittest.main()
