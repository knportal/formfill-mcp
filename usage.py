"""
FormFill MCP — Usage helpers (thin re-exports from auth.py for clarity).

Other modules can import directly from auth.py; this module exists so that
the architecture is explicit and future usage-only logic has a home.
"""

from auth import get_usage, _increment_usage, _current_year_month  # noqa: F401

__all__ = ["get_usage", "_increment_usage", "_current_year_month"]
