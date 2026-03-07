# ABOUTME: Tests for knowledge federation - MappedEvent extensions and peer learning
# ABOUTME: Covers federate_learnings, enrich_with_peer_learnings, and new MappedEvent fields

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from driftdriver.event_bridge import MappedEvent, federate_learnings
from driftdriver.contract_enrichment import enrich_with_peer_learnings, EnrichmentResult
from driftdriver.peer_registry import PeerInfo


class _FakePeerRegistry:
    """Stand-in for PeerRegistry that returns pre-configured peers."""

    def __init__(self, peers: list[PeerInfo]) -> None:
        self._peers = peers

    def peers(self) -> list[PeerInfo]:
        return list(self._peers)


class MappedEventNewFieldsTests(unittest.TestCase):
    def test_new_fields_default_empty(self) -> None:
        event = MappedEvent(
            session_id="s1",
            event_type="observation",
            project="proj",
            payload={"key": "val"},
        )
        self.assertEqual(event.peer_id, "")
        self.assertEqual(event.cross_repo_ref, "")

    def test_new_fields_set_explicitly(self) -> None:
        event = MappedEvent(
            session_id="s1",
            event_type="observation",
            project="proj",
            payload={},
            peer_id="workgraph",
            cross_repo_ref="peer:workgraph:task-42",
        )
        self.assertEqual(event.peer_id, "workgraph")
        self.assertEqual(event.cross_repo_ref, "peer:workgraph:task-42")


class FederateLearningsTests(unittest.TestCase):
    def test_federate_from_peer_knowledge_files(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            # Create peer directory with knowledge.jsonl
            peer_dir = Path(tmpdir) / "peer-project"
            wg_dir = peer_dir / ".workgraph"
            wg_dir.mkdir(parents=True)
            kb_file = wg_dir / "knowledge.jsonl"
            entries = [
                {"category": "testing", "content": "Always test edge cases", "confidence": 0.9},
                {"category": "arch", "content": "Use dependency injection", "confidence": 0.8},
            ]
            kb_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            peers = [PeerInfo(name="peer-proj", path=str(peer_dir))]
            registry = _FakePeerRegistry(peers)

            result = federate_learnings(Path("/tmp/local"), registry)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["_peer"], "peer-proj")
            self.assertEqual(result[0]["content"], "Always test edge cases")
            self.assertEqual(result[1]["_peer"], "peer-proj")
        finally:
            for root, dirs, files in os.walk(tmpdir, topdown=False):
                for f in files:
                    os.unlink(os.path.join(root, f))
                for d in dirs:
                    os.rmdir(os.path.join(root, d))
            os.rmdir(tmpdir)

    def test_federate_skips_inaccessible_peers(self) -> None:
        peers = [PeerInfo(name="gone", path="/nonexistent/path/that/doesnt/exist")]
        registry = _FakePeerRegistry(peers)
        result = federate_learnings(Path("/tmp/local"), registry)
        self.assertEqual(result, [])

    def test_federate_no_peers(self) -> None:
        registry = _FakePeerRegistry([])
        result = federate_learnings(Path("/tmp/local"), registry)
        self.assertEqual(result, [])

    def test_federate_skips_malformed_lines(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            peer_dir = Path(tmpdir) / "peer2"
            wg_dir = peer_dir / ".workgraph"
            wg_dir.mkdir(parents=True)
            kb_file = wg_dir / "knowledge.jsonl"
            kb_file.write_text(
                json.dumps({"content": "good entry", "confidence": 0.7}) + "\n"
                + "not valid json\n"
                + json.dumps({"content": "another good", "confidence": 0.8}) + "\n"
            )

            registry = _FakePeerRegistry([PeerInfo(name="p2", path=str(peer_dir))])
            result = federate_learnings(Path("/tmp/local"), registry)
            self.assertEqual(len(result), 2)
        finally:
            for root, dirs, files in os.walk(tmpdir, topdown=False):
                for f in files:
                    os.unlink(os.path.join(root, f))
                for d in dirs:
                    os.rmdir(os.path.join(root, d))
            os.rmdir(tmpdir)


class EnrichWithPeerLearningsTests(unittest.TestCase):
    def test_combines_local_and_peer(self) -> None:
        local = [
            {"category": "testing", "content": "Write integration tests for database queries", "confidence": 0.9},
        ]
        peer = [
            {"category": "testing", "content": "Database testing should use fixtures not mocks", "confidence": 0.8, "_peer": "wg"},
        ]
        result = enrich_with_peer_learnings(
            task_id="t-1",
            description="Implement database testing strategy with integration tests",
            project="proj",
            local_knowledge=local,
            peer_knowledge=peer,
        )
        self.assertIsInstance(result, EnrichmentResult)
        self.assertEqual(result.task_id, "t-1")
        self.assertGreater(result.learnings_added, 0)
        self.assertTrue(result.contract_updated)

    def test_empty_knowledge_returns_no_update(self) -> None:
        result = enrich_with_peer_learnings(
            task_id="t-2",
            description="xyzzy frobnicate quux",
            project="proj",
            local_knowledge=[],
            peer_knowledge=[],
        )
        self.assertEqual(result.learnings_added, 0)
        self.assertFalse(result.contract_updated)

    def test_peer_entries_weighted_lower(self) -> None:
        # Same content, local should score higher than peer
        entry = {"category": "testing", "content": "Write database integration tests carefully", "confidence": 0.9}
        local = [dict(entry)]
        peer = [dict(entry, _peer="wg")]

        result = enrich_with_peer_learnings(
            task_id="t-3",
            description="Write database integration tests",
            project="proj",
            local_knowledge=local,
            peer_knowledge=peer,
            max_entries=2,
        )
        # Both should appear but local should be first (higher score)
        self.assertEqual(result.learnings_added, 2)


if __name__ == "__main__":
    unittest.main()
