from __future__ import annotations

from driftdriver.model_routes import model_for_route, route_for


def test_model_for_route_reads_central_registry() -> None:
    assert model_for_route("driftdriver.hub_chat") == "claude-sonnet-4-6"


def test_route_for_includes_provider_metadata() -> None:
    route = route_for("driftdriver.intelligence_openai_signal")

    assert route.owner == "driftdriver"
    assert route.provider == "openai"
    assert route.model == "gpt-4o-mini"
