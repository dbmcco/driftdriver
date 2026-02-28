# ABOUTME: Tests for selective knowledge priming module
# ABOUTME: Verifies filtering, ranking, loading, saving, and context generation

from driftdriver.knowledge_priming import (
    KnowledgeFact, load_knowledge_base, filter_by_file_scope,
    filter_by_type, filter_by_confidence, rank_facts, prime_context, save_fact,
)


def test_load_knowledge_base_empty(tmp_path):
    kb = tmp_path / "kb.jsonl"
    assert load_knowledge_base(kb) == []


def test_load_knowledge_base_valid(tmp_path):
    kb = tmp_path / "kb.jsonl"
    kb.write_text('{"fact_id":"f1","fact_type":"gotcha","content":"Watch out","confidence":"high"}\n')
    facts = load_knowledge_base(kb)
    assert len(facts) == 1
    assert facts[0].fact_type == "gotcha"


def test_filter_by_file_scope_matches():
    facts = [
        KnowledgeFact(fact_id="1", fact_type="gotcha", content="x", affected_files=["driftdriver/cli.py"]),
        KnowledgeFact(fact_id="2", fact_type="gotcha", content="y", affected_files=["driftdriver/health.py"]),
    ]
    result = filter_by_file_scope(facts, ["driftdriver/cli.py"])
    assert len(result) == 1
    assert result[0].fact_id == "1"


def test_filter_by_file_scope_glob():
    facts = [KnowledgeFact(fact_id="1", fact_type="gotcha", content="x", affected_files=["driftdriver/*.py"])]
    result = filter_by_file_scope(facts, ["driftdriver/cli.py"])
    assert len(result) == 1


def test_filter_by_file_scope_no_scope_returns_all():
    facts = [KnowledgeFact(fact_id="1", fact_type="gotcha", content="x", affected_files=[])]
    result = filter_by_file_scope(facts, ["anything.py"])
    assert len(result) == 1


def test_filter_by_type():
    facts = [
        KnowledgeFact(fact_id="1", fact_type="gotcha", content="x"),
        KnowledgeFact(fact_id="2", fact_type="pattern", content="y"),
    ]
    result = filter_by_type(facts, ["gotcha"])
    assert len(result) == 1


def test_filter_by_confidence():
    facts = [
        KnowledgeFact(fact_id="1", fact_type="gotcha", content="x", confidence="high"),
        KnowledgeFact(fact_id="2", fact_type="gotcha", content="y", confidence="low"),
    ]
    result = filter_by_confidence(facts, "medium")
    assert len(result) == 1


def test_rank_facts_orders_by_confidence():
    facts = [
        KnowledgeFact(fact_id="1", fact_type="gotcha", content="x", confidence="low"),
        KnowledgeFact(fact_id="2", fact_type="gotcha", content="y", confidence="high"),
    ]
    ranked = rank_facts(facts)
    assert ranked[0].fact_id == "2"


def test_prime_context_returns_string(tmp_path):
    kb = tmp_path / "kb.jsonl"
    kb.write_text('{"fact_id":"f1","fact_type":"gotcha","content":"Watch out","confidence":"high"}\n')
    result = prime_context(kb)
    assert "Watch out" in result


def test_filter_by_file_scope_no_substring_match():
    """'test' must not match 'tests/test_auth.py' via substring."""
    facts = [
        KnowledgeFact(fact_id="1", fact_type="gotcha", content="x", affected_files=["test"]),
    ]
    result = filter_by_file_scope(facts, ["tests/test_auth.py"])
    assert len(result) == 0


def test_save_fact_appends(tmp_path):
    kb = tmp_path / "kb.jsonl"
    fact = KnowledgeFact(fact_id="f1", fact_type="pattern", content="Use dataclasses")
    save_fact(kb, fact)
    assert kb.exists()
    facts = load_knowledge_base(kb)
    assert len(facts) == 1
