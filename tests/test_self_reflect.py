# ABOUTME: Tests for the self-reflect phase module
# ABOUTME: Validates learning extraction from events, diffs, and test output

from driftdriver.self_reflect import (
    Learning, extract_from_events, extract_from_diff,
    extract_from_test_results, detect_repeated_patterns,
    classify_learning, format_learnings_for_review, self_reflect,
)


def test_extract_from_events_repeated_tool():
    events = [{"event": "pre_tool_use", "tool": "Edit"} for _ in range(5)]
    learnings = extract_from_events(events)
    assert any("Edit" in l.content for l in learnings)


def test_extract_from_diff_large():
    diff = "\n".join([f"+line{i}" for i in range(250)])
    learnings = extract_from_diff(diff)
    assert any("Large diff" in l.content for l in learnings)


def test_extract_from_diff_todos():
    diff = "+# TODO: fix this later\n+normal line\n+# FIXME: broken\n"
    learnings = extract_from_diff(diff)
    assert any("TODO" in l.content for l in learnings)


def test_extract_from_diff_clean():
    diff = "+clean code\n+more clean code\n"
    learnings = extract_from_diff(diff)
    assert len(learnings) == 0


def test_extract_from_test_results_slow():
    output = "5 passed in 12.5s"
    learnings = extract_from_test_results(output)
    assert any("optimization" in l.content for l in learnings)


def test_detect_repeated_patterns():
    events = [
        {"tool": "Edit", "tool_input": {"file_path": "/tmp/foo.py"}} for _ in range(4)
    ]
    learnings = detect_repeated_patterns(events)
    assert any("foo.py" in l.content for l in learnings)


def test_classify_learning():
    l = Learning(learning_type="gotcha", content="test")
    assert classify_learning(l) == "gotcha"


def test_format_learnings_for_review():
    learnings = [Learning(learning_type="pattern", content="Something useful")]
    report = format_learnings_for_review(learnings)
    assert "Something useful" in report


def test_format_learnings_empty():
    report = format_learnings_for_review([])
    assert "No learnings" in report


def test_self_reflect_combines_all():
    events = [{"event": "pre_tool_use", "tool": "Read"} for _ in range(5)]
    diff = "+# TODO: fix\n"
    result = self_reflect(events=events, diff_text=diff)
    assert len(result) >= 1
