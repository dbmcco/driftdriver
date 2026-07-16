# ABOUTME: Tests for the 'driftdriver graph-dir' machine-readable CLI command.
# ABOUTME: Covers JSON output, text output, parser registration, and conflict propagation.
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.cli import _build_parser
from driftdriver.cli.graph_dir_cmd import cmd_graph_dir, register_graph_dir_parser
from driftdriver.workgraph import WorkgraphDirectoryConflictError


class GraphDirCommandTests(unittest.TestCase):
    def test_json_output_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            stdout = io.StringIO()
            args = argparse.Namespace(dir=str(repo), json=True)
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cmd_graph_dir(args), 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["project_dir"], str(repo))
            self.assertEqual(payload["graph_dir"], str(repo / ".workgraph"))
            self.assertFalse(payload["initialized"])
            self.assertEqual(payload["source"], "default")

    def test_text_output_for_initialized_wg_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            graph = repo / ".wg"
            graph.mkdir()
            (graph / "graph.jsonl").write_text("", encoding="utf-8")
            stdout = io.StringIO()
            args = argparse.Namespace(dir=str(repo), json=False)
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cmd_graph_dir(args), 0)
            self.assertEqual(stdout.getvalue().strip(), str(graph))

    def test_registration_creates_graph_dir_parser(self):
        parser = _build_parser()
        # Parse 'graph-dir' as a subcommand to confirm registration and func binding.
        args = parser.parse_args(["--dir", "/tmp/example", "--json", "graph-dir"])
        self.assertIs(args.func, cmd_graph_dir)
        self.assertTrue(args.json)

    def test_dual_initialized_graphs_raise_conflict_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            for name in (".workgraph", ".wg"):
                graph = repo / name
                graph.mkdir()
                (graph / "graph.jsonl").write_text("", encoding="utf-8")
            stdout = io.StringIO()
            args = argparse.Namespace(dir=str(repo), json=True)
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(WorkgraphDirectoryConflictError):
                    cmd_graph_dir(args)
            self.assertEqual(stdout.getvalue(), "")


class GraphDirRegistrationTests(unittest.TestCase):
    def test_register_graph_dir_parser_binds_func(self):
        subparsers_holder = argparse.ArgumentParser().add_subparsers(dest="cmd")
        register_graph_dir_parser(subparsers_holder)
        parser = _build_parser()
        args = parser.parse_args(["graph-dir"])
        self.assertIs(args.func, cmd_graph_dir)
