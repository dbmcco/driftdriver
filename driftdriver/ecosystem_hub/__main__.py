# ABOUTME: Entry point for `python -m driftdriver.ecosystem_hub`.
# ABOUTME: Delegates to the main() function in the server module.
from __future__ import annotations

from .server import main

raise SystemExit(main())
