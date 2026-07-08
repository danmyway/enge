"""Guard: dispatch.__main__ must not have a module-level artifact_type global."""

import ast
import inspect
import unittest

from enge.dispatch import __main__ as dispatch_main


class TestNoArtifactTypeGlobal(unittest.TestCase):
    """Structural guard against reintroduction of the artifact_type module global."""

    def test_no_module_level_artifact_type_binding(self):
        self.assertFalse(
            hasattr(dispatch_main, "artifact_type"),
            "dispatch.__main__ must not export a module-level 'artifact_type' attribute",
        )

    def test_no_global_artifact_type_statement(self):
        source = inspect.getsource(dispatch_main)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                self.assertNotIn(
                    "artifact_type",
                    node.names,
                    "dispatch.__main__ must not contain 'global artifact_type'",
                )


if __name__ == "__main__":
    unittest.main()
