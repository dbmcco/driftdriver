from __future__ import annotations

import unittest


class TestImports(unittest.TestCase):
    def test_imports(self) -> None:
        import driftdriver.cli  # noqa: F401
        import driftdriver.health  # noqa: F401
        import driftdriver.install  # noqa: F401
        import driftdriver.updates  # noqa: F401
        import driftdriver.workgraph  # noqa: F401


if __name__ == "__main__":
    unittest.main()
