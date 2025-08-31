"""Compatibility shim for the root-level import `import trade`.

This module delegates to `src.trade` where the real implementation lives.
It keeps imports working for older scripts/tests that import from the repo root.
"""

from src.trade import *  # re-export everything for compatibility  # noqa: F401,F403
