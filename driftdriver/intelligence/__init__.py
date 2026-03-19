# ABOUTME: Ecosystem intelligence package for persistent upstream and community signal handling
# ABOUTME: Lazily exposes migration helpers without importing db.py during module execution

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = ["MigrationResult", "PostgresConfig", "ensure_database_and_apply_migrations"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module("driftdriver.intelligence.db")
    return getattr(module, name)
