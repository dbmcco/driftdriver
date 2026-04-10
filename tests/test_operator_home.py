from __future__ import annotations

from driftdriver.ecosystem_hub.operator_home import build_operator_home


def test_build_operator_home_promotes_control_plane_failures_into_now() -> None:
    snapshot = {
        "generated_at": "2026-04-10T20:00:00+00:00",
        "repos": [],
        "northstardrift": {
            "summary": {
                "overall_score": 82.0,
                "overall_trend": "regressing",
                "overall_tier": "watch",
            }
        },
        "overview": {"repos_with_errors": 1},
        "control_plane": {"errors": ["factory cycle failed"], "hub_available": True},
    }

    payload = build_operator_home(snapshot=snapshot, decisions=[], notification_ledger=[])

    assert payload["now"][0]["kind"] == "control_plane"
    assert payload["scorecard"]["status"] == "red"


def test_build_operator_home_keeps_human_items_in_decide() -> None:
    decision = {
        "id": "dec-20260410-abc123",
        "repo": "paia-agents",
        "status": "pending",
        "question": "Adopt the Derek prompt fix?",
        "category": "agent_health",
        "context": {
            "agent_member": "derek",
            "severity": "medium",
            "confidence": 0.78,
            "full_view": {"tab": "factory", "focus": "decision:dec-20260410-abc123"},
        },
        "created_at": "2026-04-10T19:50:00+00:00",
    }

    payload = build_operator_home(
        snapshot={"repos": [], "northstardrift": {"summary": {}}},
        decisions=[decision],
        notification_ledger=[],
    )

    assert payload["decide"][0]["decision_id"] == "dec-20260410-abc123"
    assert payload["decide"][0]["full_view"]["tab"] == "factory"


def test_build_operator_home_moves_low_signal_or_stale_items_into_watch() -> None:
    decision = {
        "id": "dec-20260410-stale01",
        "repo": "paia-agents",
        "status": "pending",
        "question": "Minor CLAUDE.md wording fix?",
        "category": "agent_health",
        "context": {"severity": "low", "confidence": 0.41},
        "created_at": "2026-04-05T12:00:00+00:00",
    }

    payload = build_operator_home(
        snapshot={"repos": [], "northstardrift": {"summary": {}}},
        decisions=[decision],
        notification_ledger=[],
    )

    assert payload["decide"] == []
    assert payload["watch"][0]["decision_id"] == "dec-20260410-stale01"


def test_scorecard_reports_autonomy_and_convergence_fields() -> None:
    snapshot = {
        "repos": [],
        "northstardrift": {
            "summary": {
                "overall_score": 61.0,
                "overall_trend": "improving",
                "overall_tier": "healthy",
            }
        },
        "overview": {},
    }
    ledger = [
        {"decision_id": "dec-1", "delivery_status": "sent", "route": "digest", "provenance": {}},
        {"decision_id": "dec-2", "delivery_status": "autonomous_closed", "route": "digest", "provenance": {}},
    ]

    payload = build_operator_home(snapshot=snapshot, decisions=[], notification_ledger=ledger)

    assert payload["scorecard"]["autonomous_this_week"] == 1
    assert payload["scorecard"]["convergence_trend"] == "improving"
    assert "control_plane" in payload["domains"]
    assert "autonomy" in payload["domains"]
    assert "convergence" in payload["domains"]
