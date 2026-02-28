from __future__ import annotations

import unittest


class TestImports(unittest.TestCase):
    def test_imports(self) -> None:
        import driftdriver.cli  # noqa: F401
        import driftdriver.health  # noqa: F401
        import driftdriver.install  # noqa: F401
        import driftdriver.updates  # noqa: F401
        import driftdriver.workgraph  # noqa: F401

    def test_import_workgraph(self) -> None:
        from driftdriver import workgraph
        assert hasattr(workgraph, 'find_workgraph_dir')
        assert hasattr(workgraph, 'load_workgraph')

    def test_import_wire(self) -> None:
        from driftdriver import wire
        assert hasattr(wire, 'cmd_verify')


if __name__ == "__main__":
    unittest.main()
