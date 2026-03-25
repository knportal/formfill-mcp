"""
FormFill MCP — API key management & usage tracking.

Two SQLite databases:
  keys.db  — API keys, tiers, Stripe customer ids
  usage.db — per-key monthly fill counts

All public functions return (ok: bool, error_message: str | None).
"""

import logging
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import KEYS_DB, USAGE_DB, FREE_MONTHLY_LIMIT, UPGRADE_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

KEYS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key             TEXT PRIMARY KEY,
    tier            TEXT NOT NULL DEFAULT 'free',   -- 'free' | 'pro'
    stripe_customer TEXT,
    created_at      TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1
);
"""

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    key         TEXT NOT NULL,
    year_month  TEXT NOT NULL,   -- e.g. '2026-03'
    fill_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, year_month)
);
"""


@contextmanager
def _keys_conn():
    conn = sqlite3.connect(KEYS_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(KEYS_SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


@contextmanager
def _usage_conn():
    conn = sqlite3.connect(USAGE_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(USAGE_SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def _generate_key(tier: str) -> str:
    """Generate a new API key in the correct format for the given tier."""
    prefix = "ff_live" if tier == "pro" else "ff_free"
    return f"{prefix}_{secrets.token_urlsafe(24)}"  # ~32 random chars (base64url)


def create_key(tier: str = "free", stripe_customer: str | None = None) -> str:
    """
    Create a new API key, persist it, and return the key string.

    Args:
        tier: 'free' or 'pro'
        stripe_customer: optional Stripe customer ID to associate

    Returns:
        The new API key string.
    """
    if tier not in ("free", "pro"):
        raise ValueError(f"Invalid tier '{tier}'. Must be 'free' or 'pro'.")

    key = _generate_key(tier)
    now = datetime.utcnow().isoformat()

    with _keys_conn() as conn:
        conn.execute(
            "INSERT INTO api_keys (key, tier, stripe_customer, created_at) VALUES (?, ?, ?, ?)",
            (key, tier, stripe_customer, now),
        )
        conn.commit()

    logger.info("Created API key tier=%s key=%s…", tier, key[:16])
    return key


def list_keys() -> list[dict]:
    """Return all API keys with their metadata."""
    with _keys_conn() as conn:
        rows = conn.execute(
            "SELECT key, tier, stripe_customer, created_at, active FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def set_key_tier(key: str, tier: str, stripe_customer: str | None = None) -> bool:
    """Update the tier for an existing API key (called by Stripe webhook)."""
    with _keys_conn() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET tier = ?, stripe_customer = COALESCE(?, stripe_customer) WHERE key = ?",
            (tier, stripe_customer, key),
        )
        conn.commit()
        changed = cur.rowcount > 0

    if changed:
        logger.info("Updated key %s… to tier=%s", key[:16], tier)
    return changed


def deactivate_key(key: str) -> bool:
    """Mark a key as inactive (soft delete)."""
    with _keys_conn() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET active = 0 WHERE key = ?", (key,)
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Auth + quota check (the hot path)
# ---------------------------------------------------------------------------

def _current_year_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def validate_and_charge(api_key: str) -> tuple[bool, str | None]:
    """
    Validate the API key and check/increment the usage counter.

    Returns:
        (True, None)              — key is valid and quota has been charged
        (False, error_message)   — auth or quota failure; nothing was charged
    """
    if not api_key or not isinstance(api_key, str):
        return False, "Missing api_key parameter."

    # 1. Look up the key
    with _keys_conn() as kconn:
        row = kconn.execute(
            "SELECT tier, active FROM api_keys WHERE key = ?", (api_key,)
        ).fetchone()

    if row is None:
        return False, (
            "Invalid API key. Generate a free key at https://formfill.plenitudo.ai"
        )
    if not row["active"]:
        return False, "This API key has been deactivated. Visit formfill.plenitudo.ai."

    tier = row["tier"]

    # 2. Pro tier — unlimited
    if tier == "pro":
        _increment_usage(api_key)
        return True, None

    # 3. Free tier — check monthly limit
    ym = _current_year_month()
    with _usage_conn() as uconn:
        usage_row = uconn.execute(
            "SELECT fill_count FROM usage WHERE key = ? AND year_month = ?",
            (api_key, ym),
        ).fetchone()

    current_count = usage_row["fill_count"] if usage_row else 0

    if current_count >= FREE_MONTHLY_LIMIT:
        return False, (
            f"Monthly limit reached ({FREE_MONTHLY_LIMIT} fills/month on the free tier). "
            f"Upgrade to Pro at {UPGRADE_URL}"
        )

    _increment_usage(api_key)
    return True, None


def _increment_usage(api_key: str) -> None:
    """Upsert the usage counter for the current month."""
    ym = _current_year_month()
    with _usage_conn() as conn:
        conn.execute(
            """
            INSERT INTO usage (key, year_month, fill_count) VALUES (?, ?, 1)
            ON CONFLICT(key, year_month) DO UPDATE SET fill_count = fill_count + 1
            """,
            (api_key, ym),
        )
        conn.commit()


def get_usage(api_key: str) -> dict:
    """Return usage stats for an API key (current month + all time)."""
    ym = _current_year_month()
    with _usage_conn() as conn:
        rows = conn.execute(
            "SELECT year_month, fill_count FROM usage WHERE key = ? ORDER BY year_month DESC",
            (api_key,),
        ).fetchall()

    history = [dict(r) for r in rows]
    current = next((r["fill_count"] for r in history if r["year_month"] == ym), 0)
    total = sum(r["fill_count"] for r in history)

    return {
        "current_month": ym,
        "current_month_fills": current,
        "total_fills": total,
        "history": history,
    }
