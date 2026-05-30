"""US-only stock auto-screening, split out from the shared ah_screener core.

This sub-package reuses the ah_screener data/compute core (``sources.us_client``,
``storage``, ``scoring``, ``technical``, ``classification``, ``expert_model``,
``reporting`` helpers, ``scheduler``) but keeps an independent DuckDB, CLI,
scoring profile and daily pre-market report. See ``docs/us-screener.md``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
