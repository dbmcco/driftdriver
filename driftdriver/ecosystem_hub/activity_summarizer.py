# ABOUTME: LLM-based activity summarizer for the ecosystem hub.
# ABOUTME: Calls Claude Haiku to produce 2-3 sentence repo activity summaries.
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)
_MODEL = "claude-haiku-4-5-20251001"


def _build_prompt(digest: dict[str, Any]) -> str:
    """Build a prompt for Haiku to summarize recent repo activity."""
    name = digest["name"]
    subjects = digest["windows"]["7d"]["subjects"]
    files = digest.get("changed_files", [])

    commit_lines = "\n".join(f"- {s}" for s in subjects[:20])
    file_dirs = sorted(set(
        "/".join(f.split("/")[:2]) if "/" in f else f
        for f in files[:20]
    ))
    file_summary = ", ".join(file_dirs[:10]) if file_dirs else "various files"

    return (
        f"Repo: {name}\n"
        f"Recent commits (last 7 days):\n{commit_lines}\n"
        f"Changed files: {file_summary}\n\n"
        f"Write 2-3 sentences describing what's been happening in this repo. "
        f"Be specific about what was built or fixed. No filler."
    )


def summarize_repo(digest: dict[str, Any], *, client: Any = None) -> dict[str, Any]:
    """
    Add or refresh the LLM summary for a single repo digest.
    Returns the digest with summary/summary_hash updated.
    Skips if hash matches or no commits exist.
    """
    last_hash = digest.get("last_commit_hash")
    if not last_hash:
        return digest

    if digest.get("summary_hash") == last_hash and digest.get("summary"):
        return digest

    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:
            _LOG.debug("anthropic not available, skipping summarization")
            return digest

    prompt = _build_prompt(digest)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.content[0].text.strip()
        return {**digest, "summary": summary, "summary_hash": last_hash}
    except Exception as exc:
        _LOG.debug("Haiku summarization failed for %s: %s", digest["name"], exc)
        return {**digest, "summary": None, "summary_hash": None}


def summarize_all(digests: list[dict[str, Any]], *, client: Any = None) -> list[dict[str, Any]]:
    """Run summarize_repo for all digests that need a new summary."""
    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:
            _LOG.debug("anthropic not available, returning digests unsummarized")
            return digests

    return [summarize_repo(d, client=client) for d in digests]
