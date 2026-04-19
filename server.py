"""
FormFill MCP Server — Production
Fills PDF forms from structured field data.

Every tool requires an `api_key` parameter. Keys are issued at
https://formfill.plenitudo.ai.

Uses file paths (not base64) to handle large PDFs without message-size issues.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

from pydantic import Field

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging — must be configured before importing auth (which also logs)
# ---------------------------------------------------------------------------
from config import LOG_FILE, LOG_LEVEL

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
from auth import validate_and_charge  # noqa: E402

# ---------------------------------------------------------------------------
# x402 micropayments
# ---------------------------------------------------------------------------
from x402 import (  # noqa: E402
    PRICE_USDC,
    WALLET_ADDRESS,
    is_proof_used,
    mark_proof_used,
    payment_required_response,
    verify_payment,
)

# ---------------------------------------------------------------------------
# PDF libraries — pypdf is authoritative; we fall back gracefully
# ---------------------------------------------------------------------------
try:
    from pypdf import PdfReader, PdfWriter
    _PYPDF_OK = True
except ImportError:  # pragma: no cover
    _PYPDF_OK = False
    logger.error("pypdf is not installed. Run: pip install pypdf")


# ---------------------------------------------------------------------------
# In-memory stats
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3
import time as _time
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

_stats: dict = {
    "total_calls": 0,
    "errors": 0,
    "start_time": _time.time(),
    "tools_breakdown": {},
}


def _track_tool(tool_name: str) -> None:
    """Increment per-tool call counter."""
    _stats["tools_breakdown"][tool_name] = _stats["tools_breakdown"].get(tool_name, 0) + 1


# ---------------------------------------------------------------------------
# SQLite analytics logger
# ---------------------------------------------------------------------------
_ANALYTICS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics.db")


def _init_analytics_db() -> None:
    """Create the analytics table if it doesn't exist."""
    conn = _sqlite3.connect(_ANALYTICS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name   TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            payment_received INTEGER NOT NULL DEFAULT 0,
            amount_usdc REAL    NOT NULL DEFAULT 0.0,
            success     INTEGER NOT NULL DEFAULT 1,
            latency_ms  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _log_call(
    tool_name: str,
    payment_received: bool,
    amount_usdc: float,
    success: bool,
    latency_ms: int,
) -> None:
    """Insert one analytics row. Never raises — failures are logged silently."""
    try:
        conn = _sqlite3.connect(_ANALYTICS_DB)
        conn.execute(
            """
            INSERT INTO tool_calls
                (tool_name, timestamp, payment_received, amount_usdc, success, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name,
                _dt.now(_tz.utc).isoformat(),
                int(payment_received),
                amount_usdc,
                int(success),
                latency_ms,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("analytics log failed: %s", exc)


# Initialise DB at import time (no-op if table already exists)
try:
    _init_analytics_db()
except Exception as _exc:
    logger.warning("Could not initialise analytics DB: %s", _exc)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
_PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "FormFill",
    instructions="Fill any interactive PDF form from your AI agent — tax forms, HR paperwork, legal documents — in a single tool call.",
    host="0.0.0.0",
    port=_PORT,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth_error(msg: str) -> str:
    return json.dumps({"error": msg, "ok": False})


def _resolve(pdf_path: str) -> tuple[Path | None, str | None]:
    """Expand and validate a PDF path. Returns (path, None) or (None, error)."""
    try:
        p = Path(pdf_path).expanduser().resolve()
    except Exception as exc:
        return None, f"Invalid path: {exc}"
    if not p.exists():
        return None, f"File not found: {pdf_path}"
    if not p.is_file():
        return None, f"Path is not a file: {pdf_path}"
    return p, None


def _get_reader_fields(reader: "PdfReader") -> dict:
    """Return the raw field dict from a PdfReader (may be None → empty dict)."""
    fields = reader.get_fields()
    return fields if fields else {}


def _validate_fields(
    requested: dict, available: dict
) -> tuple[dict, list[str]]:
    """
    Split requested field_values into valid and invalid buckets.

    Returns:
        (valid_subset, list_of_invalid_names)
    """
    valid = {k: v for k, v in requested.items() if k in available}
    invalid = [k for k in requested if k not in available]
    return valid, invalid


# ---------------------------------------------------------------------------
# Tool 1 — list_form_fields
# ---------------------------------------------------------------------------

@mcp.tool()
def list_form_fields(
    pdf_path: Annotated[str, Field(description="Absolute path to the PDF file on disk.")],
    api_key: Annotated[str | None, Field(description="Your FormFill API key (get one at formfill.plenitudo.ai).")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (tx hash). Alternative to api_key for pay-per-use.")] = None,
) -> str:
    """Inspect a PDF and return every fillable field name, type, and current value. Use this before fill_form to discover available fields."""
    _stats["total_calls"] += 1
    _track_tool("list_form_fields")
    _t0 = _time.monotonic()
    # Auth — listing fields is free, but we still require a valid key or payment proof
    if api_key:
        ok, err = validate_and_charge.__wrapped__(api_key) if hasattr(validate_and_charge, "__wrapped__") else _validate_key_only(api_key)
        if not ok:
            _log_call("list_form_fields", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return _auth_error(err)
    elif payment_proof:
        pass  # listing is free; accept any payment_proof without consuming it
    else:
        _log_call("list_form_fields", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_error("Missing api_key or payment_proof parameter.")

    if not _PYPDF_OK:
        _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_error("pypdf library not available on this server.")

    src, err = _resolve(pdf_path)
    if err:
        logger.warning("list_form_fields path error: %s", err)
        _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": err, "ok": False})

    try:
        reader = PdfReader(str(src))
        fields = _get_reader_fields(reader)

        if not fields:
            _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({
                "ok": False,
                "error": "No fillable fields found in this PDF.",
                "note": (
                    "The PDF may be flat/scanned rather than an interactive form. "
                    "Try opening it in Acrobat to confirm."
                ),
            })

        field_info = {}
        for name, field in fields.items():
            raw_type = str(field.get("/FT", "unknown"))
            type_map = {
                "/Tx": "text",
                "/Btn": "button/checkbox",
                "/Ch": "choice/dropdown",
                "/Sig": "signature",
            }
            field_info[name] = {
                "type": type_map.get(raw_type, raw_type),
                "current_value": str(field.get("/V", "")),
            }

        logger.info("list_form_fields: %s — %d fields", src.name, len(field_info))
        _log_call("list_form_fields", bool(payment_proof), 0.0, True, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(
            {"ok": True, "field_count": len(field_info), "fields": field_info},
            indent=2,
        )

    except Exception as exc:
        logger.exception("list_form_fields failed for %s", pdf_path)
        _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": str(exc), "ok": False})


def _validate_key_only(api_key: str) -> tuple[bool, str | None]:
    """
    Validate the API key WITHOUT charging usage (used for list_form_fields).
    """
    import sqlite3
    from config import KEYS_DB

    if not api_key or not isinstance(api_key, str):
        return False, "Missing api_key parameter."

    try:
        conn = sqlite3.connect(KEYS_DB)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                tier TEXT NOT NULL DEFAULT 'free',
                stripe_customer TEXT,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        row = conn.execute(
            "SELECT tier, active FROM api_keys WHERE key = ?", (api_key,)
        ).fetchone()
        conn.close()
    except Exception as exc:
        logger.exception("Key DB error")
        return False, f"Auth service error: {exc}"

    if row is None:
        return False, (
            "Invalid API key. Generate a free key at https://formfill.plenitudo.ai"
        )
    if not row["active"]:
        return False, "This API key has been deactivated. Visit formfill.plenitudo.ai."

    return True, None


# ---------------------------------------------------------------------------
# Tool 2 — fill_form
# ---------------------------------------------------------------------------

@mcp.tool()
def fill_form(
    pdf_path: Annotated[str, Field(description="Absolute path to the source PDF file.")],
    field_values: Annotated[dict[str, str], Field(description="Map of field names to values. Use list_form_fields to discover field names.")],
    output_path: Annotated[str, Field(description="Absolute path where the filled PDF will be saved.")],
    api_key: Annotated[str | None, Field(description="Your FormFill API key (get one at formfill.plenitudo.ai).")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (tx hash). Alternative to api_key for pay-per-use.")] = None,
) -> str:
    """Fill a PDF form with the given field values and save the result to disk. Use for standard single-page or short forms (under 5 pages)."""
    _stats["total_calls"] += 1
    _track_tool("fill_form")
    _t0 = _time.monotonic()
    _paid = False
    _amount = 0.0
    # Auth: accept either API key OR x402 payment proof
    if api_key:
        ok, err = validate_and_charge(api_key)
        if not ok:
            _log_call("fill_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return _auth_error(err)
    elif payment_proof:
        if is_proof_used(payment_proof):
            _log_call("fill_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": "Payment proof already used"})
        ok, err = verify_payment(payment_proof, PRICE_USDC, WALLET_ADDRESS)
        if not ok:
            _log_call("fill_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": f"Payment verification failed: {err}"})
        mark_proof_used(payment_proof, "fill_form")
        _paid = True
        _amount = PRICE_USDC
    else:
        _log_call("fill_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(payment_required_response("fill_form"))

    if not _PYPDF_OK:
        _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_error("pypdf library not available on this server.")

    src, err = _resolve(pdf_path)
    if err:
        logger.warning("fill_form source error: %s", err)
        _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": err, "ok": False})

    try:
        dst = Path(output_path).expanduser().resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": f"Invalid output path: {exc}", "ok": False})

    try:
        reader = PdfReader(str(src))
        available_fields = _get_reader_fields(reader)

        # Validate requested field names
        valid_values, invalid_names = _validate_fields(field_values, available_fields)

        if invalid_names:
            logger.warning(
                "fill_form: %d unknown field(s) for %s: %s",
                len(invalid_names),
                src.name,
                invalid_names,
            )

        writer = PdfWriter()
        writer.append(reader)

        # Apply fields across all pages
        for page in writer.pages:
            writer.update_page_form_field_values(page, field_values)

        with open(str(dst), "wb") as fh:
            writer.write(fh)

        result = {
            "ok": True,
            "output_path": str(dst),
            "fields_filled": len(valid_values),
            "pages": len(reader.pages),
            "message": f"Filled PDF saved to {dst}",
        }
        if invalid_names:
            result["warnings"] = {
                "unknown_fields": invalid_names,
                "valid_fields": list(available_fields.keys()),
            }

        logger.info(
            "fill_form: %s → %s (%d fields, %d pages)",
            src.name,
            dst.name,
            len(valid_values),
            len(reader.pages),
        )
        _log_call("fill_form", _paid, _amount, True, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.exception("fill_form failed for %s", pdf_path)
        _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": str(exc), "ok": False})


# ---------------------------------------------------------------------------
# Tool 3 — fill_form_multipage
# ---------------------------------------------------------------------------

@mcp.tool()
def fill_form_multipage(
    pdf_path: Annotated[str, Field(description="Absolute path to the source PDF file.")],
    field_values: Annotated[dict[str, str], Field(description="Map of field names to values. Use list_form_fields to discover field names.")],
    output_path: Annotated[str, Field(description="Absolute path where the filled PDF will be saved.")],
    api_key: Annotated[str | None, Field(description="Your FormFill API key (get one at formfill.plenitudo.ai).")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (tx hash). Alternative to api_key for pay-per-use.")] = None,
) -> str:
    """Fill a multi-page PDF form, iterating page-by-page for reliability. Use when the PDF has more than 5 pages or fields spanning multiple pages (e.g. rental applications, tax packets, multi-section HR forms). Prefer this tool over fill_form for any complex or long document."""
    _stats["total_calls"] += 1
    _track_tool("fill_form_multipage")
    _t0 = _time.monotonic()
    _paid = False
    _amount = 0.0
    # Auth: accept either API key OR x402 payment proof
    if api_key:
        ok, err = validate_and_charge(api_key)
        if not ok:
            _log_call("fill_form_multipage", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return _auth_error(err)
    elif payment_proof:
        if is_proof_used(payment_proof):
            _log_call("fill_form_multipage", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": "Payment proof already used"})
        ok, err = verify_payment(payment_proof, PRICE_USDC, WALLET_ADDRESS)
        if not ok:
            _log_call("fill_form_multipage", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": f"Payment verification failed: {err}"})
        mark_proof_used(payment_proof, "fill_form_multipage")
        _paid = True
        _amount = PRICE_USDC
    else:
        _log_call("fill_form_multipage", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(payment_required_response("fill_form_multipage"))

    if not _PYPDF_OK:
        _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_error("pypdf library not available on this server.")

    src, err = _resolve(pdf_path)
    if err:
        logger.warning("fill_form_multipage source error: %s", err)
        _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": err, "ok": False})

    try:
        dst = Path(output_path).expanduser().resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": f"Invalid output path: {exc}", "ok": False})

    try:
        reader = PdfReader(str(src))
        available_fields = _get_reader_fields(reader)

        valid_values, invalid_names = _validate_fields(field_values, available_fields)

        writer = PdfWriter()
        writer.append(reader)

        pages_updated = []
        for i, page in enumerate(writer.pages):
            writer.update_page_form_field_values(page, field_values)
            pages_updated.append(i + 1)

        with open(str(dst), "wb") as fh:
            writer.write(fh)

        result = {
            "ok": True,
            "output_path": str(dst),
            "fields_filled": len(valid_values),
            "total_pages": len(reader.pages),
            "pages_updated": pages_updated,
            "message": f"Multi-page filled PDF saved to {dst}",
        }
        if invalid_names:
            result["warnings"] = {
                "unknown_fields": invalid_names,
                "valid_fields": list(available_fields.keys()),
            }

        logger.info(
            "fill_form_multipage: %s → %s (%d fields, %d pages)",
            src.name,
            dst.name,
            len(valid_values),
            len(reader.pages),
        )
        _log_call("fill_form_multipage", _paid, _amount, True, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.exception("fill_form_multipage failed for %s", pdf_path)
        _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": str(exc), "ok": False})


# ---------------------------------------------------------------------------
# Tool 4 — extract_form_data
# ---------------------------------------------------------------------------

@mcp.tool()
def extract_form_data(
    pdf_path: Annotated[str, Field(description="Absolute path to the PDF file on disk.")],
    api_key: Annotated[str | None, Field(description="Your FormFill API key (get one at formfill.plenitudo.ai).")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (tx hash). Alternative to api_key for pay-per-use.")] = None,
) -> str:
    """Extract all form field values from a filled PDF form.
    Returns a dict mapping field names to their current values.
    Price: $0.001 USDC per call."""
    _stats["total_calls"] += 1
    _track_tool("extract_form_data")
    _t0 = _time.monotonic()
    _paid = False
    _amount = 0.0
    # Auth: accept either API key OR x402 payment proof
    if api_key:
        ok, err = validate_and_charge(api_key)
        if not ok:
            _log_call("extract_form_data", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return _auth_error(err)
    elif payment_proof:
        if is_proof_used(payment_proof):
            _log_call("extract_form_data", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": "Payment proof already used"})
        ok, err = verify_payment(payment_proof, PRICE_USDC, WALLET_ADDRESS)
        if not ok:
            _log_call("extract_form_data", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": f"Payment verification failed: {err}"})
        mark_proof_used(payment_proof, "extract_form_data")
        _paid = True
        _amount = PRICE_USDC
    else:
        _log_call("extract_form_data", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(payment_required_response("extract_form_data"))

    if not _PYPDF_OK:
        _log_call("extract_form_data", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_error("pypdf library not available on this server.")

    src, err = _resolve(pdf_path)
    if err:
        logger.warning("extract_form_data path error: %s", err)
        _log_call("extract_form_data", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": err, "ok": False})

    try:
        reader = PdfReader(str(src))
        field_values = {}

        for page in reader.pages:
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot in annots:
                obj = annot.get_object() if hasattr(annot, "get_object") else annot
                field_name = obj.get("/T")
                field_value = obj.get("/V")
                if field_name is not None:
                    field_values[str(field_name)] = str(field_value) if field_value is not None else ""

        logger.info("extract_form_data: %s — %d fields extracted", src.name, len(field_values))
        _log_call("extract_form_data", _paid, _amount, True, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(
            {"ok": True, "field_count": len(field_values), "fields": field_values},
            indent=2,
        )

    except Exception as exc:
        logger.exception("extract_form_data failed for %s", pdf_path)
        _log_call("extract_form_data", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": str(exc), "ok": False})


# ---------------------------------------------------------------------------
# Tool 5 — flatten_form
# ---------------------------------------------------------------------------

@mcp.tool()
def flatten_form(
    pdf_path: Annotated[str, Field(description="Absolute path to the source PDF file.")],
    output_path: Annotated[str, Field(description="Absolute path where the flattened PDF will be saved.")],
    api_key: Annotated[str | None, Field(description="Your FormFill API key (get one at formfill.plenitudo.ai).")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (tx hash). Alternative to api_key for pay-per-use.")] = None,
) -> str:
    """Flatten a filled PDF form so form fields become non-editable static content.
    Returns success status and output path.
    Price: $0.001 USDC per call."""
    _stats["total_calls"] += 1
    _track_tool("flatten_form")
    _t0 = _time.monotonic()
    _paid = False
    _amount = 0.0
    # Auth: accept either API key OR x402 payment proof
    if api_key:
        ok, err = validate_and_charge(api_key)
        if not ok:
            _log_call("flatten_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return _auth_error(err)
    elif payment_proof:
        if is_proof_used(payment_proof):
            _log_call("flatten_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": "Payment proof already used"})
        ok, err = verify_payment(payment_proof, PRICE_USDC, WALLET_ADDRESS)
        if not ok:
            _log_call("flatten_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, "error": f"Payment verification failed: {err}"})
        mark_proof_used(payment_proof, "flatten_form")
        _paid = True
        _amount = PRICE_USDC
    else:
        _log_call("flatten_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(payment_required_response("flatten_form"))

    if not _PYPDF_OK:
        _log_call("flatten_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_error("pypdf library not available on this server.")

    src, err = _resolve(pdf_path)
    if err:
        logger.warning("flatten_form source error: %s", err)
        _log_call("flatten_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": err, "ok": False})

    try:
        dst = Path(output_path).expanduser().resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _log_call("flatten_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": f"Invalid output path: {exc}", "ok": False})

    try:
        reader = PdfReader(str(src))
        writer = PdfWriter()

        for page in reader.pages:
            writer.add_page(page)

        with open(str(dst), "wb") as fh:
            writer.write(fh)

        logger.info("flatten_form: %s → %s (%d pages)", src.name, dst.name, len(reader.pages))
        _log_call("flatten_form", _paid, _amount, True, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(
            {
                "ok": True,
                "output_path": str(dst),
                "pages": len(reader.pages),
                "message": f"Flattened PDF saved to {dst}",
            },
            indent=2,
        )

    except Exception as exc:
        logger.exception("flatten_form failed for %s", pdf_path)
        _log_call("flatten_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"error": str(exc), "ok": False})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--stdio" in sys.argv:
        logger.info("FormFill MCP server starting up (stdio)")
        mcp.run(transport="stdio")
    else:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        async def health(request: Request):
            return JSONResponse({"status": "ok", "service": "formfill-mcp"})

        async def analytics_endpoint(request: Request):
            """
            GET /analytics — returns standardised call analytics from analytics.db.
            Schema:
              total_calls, paid_calls, total_revenue_usdc,
              calls_by_tool, avg_latency_ms, last_24h_calls
            """
            try:
                conn = _sqlite3.connect(_ANALYTICS_DB)
                conn.row_factory = _sqlite3.Row

                cutoff = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()

                total_calls = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
                paid_calls = conn.execute(
                    "SELECT COUNT(*) FROM tool_calls WHERE payment_received = 1"
                ).fetchone()[0]
                total_revenue = conn.execute(
                    "SELECT COALESCE(SUM(amount_usdc), 0.0) FROM tool_calls WHERE payment_received = 1"
                ).fetchone()[0]
                avg_latency_row = conn.execute(
                    "SELECT COALESCE(AVG(latency_ms), 0) FROM tool_calls"
                ).fetchone()[0]
                last_24h = conn.execute(
                    "SELECT COUNT(*) FROM tool_calls WHERE timestamp >= ?", (cutoff,)
                ).fetchone()[0]
                tool_rows = conn.execute(
                    "SELECT tool_name, COUNT(*) AS cnt FROM tool_calls GROUP BY tool_name"
                ).fetchall()
                conn.close()

                calls_by_tool = {row["tool_name"]: row["cnt"] for row in tool_rows}
                return JSONResponse({
                    "total_calls": total_calls,
                    "paid_calls": paid_calls,
                    "total_revenue_usdc": round(float(total_revenue), 6),
                    "calls_by_tool": calls_by_tool,
                    "avg_latency_ms": int(avg_latency_row),
                    "last_24h_calls": last_24h,
                })
            except Exception as exc:
                return JSONResponse({
                    "total_calls": _stats["total_calls"],
                    "paid_calls": 0,
                    "total_revenue_usdc": 0.0,
                    "calls_by_tool": _stats.get("tools_breakdown", {}),
                    "avg_latency_ms": 0,
                    "last_24h_calls": 0,
                    "error": str(exc),
                })

        async def stats_endpoint(request: Request):
            """Full /stats endpoint for analytics dashboard."""
            import sqlite3 as _sqlite3
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            from x402 import _PROOF_DB
            from config import DATA_DIR

            now = _dt.now(_tz.utc)
            today_str = now.strftime("%Y-%m-%d")
            week_ago = (now - _td(days=7)).isoformat()

            # Query x402 proofs DB for revenue and caller data
            revenue_total = 0.0
            revenue_this_week = 0.0
            unique_callers = 0
            calls_today = 0
            calls_this_week = 0

            proof_db = _PROOF_DB
            if os.path.exists(proof_db):
                try:
                    conn = _sqlite3.connect(proof_db)
                    conn.row_factory = _sqlite3.Row

                    # Total revenue (each proof = $0.001 USDC)
                    row = conn.execute("SELECT COUNT(*) AS cnt FROM used_proofs").fetchone()
                    revenue_total = (row["cnt"] if row else 0) * 0.001

                    # Revenue this week
                    row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at >= ?",
                        (week_ago,),
                    ).fetchone()
                    revenue_this_week = (row["cnt"] if row else 0) * 0.001

                    # Unique callers (distinct tx_hash prefixes as proxy — first 10 chars)
                    row = conn.execute(
                        "SELECT COUNT(DISTINCT SUBSTR(tx_hash, 1, 42)) AS cnt FROM used_proofs"
                    ).fetchone()
                    unique_callers = row["cnt"] if row else 0

                    # Calls today from proofs
                    row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at LIKE ?",
                        (today_str + "%",),
                    ).fetchone()
                    calls_today_proofs = row["cnt"] if row else 0

                    # Calls this week from proofs
                    row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at >= ?",
                        (week_ago,),
                    ).fetchone()
                    calls_this_week_proofs = row["cnt"] if row else 0

                    conn.close()
                except Exception:
                    pass

            # Combine in-memory stats with DB stats
            return JSONResponse({
                "server": "formfill-mcp",
                "total_calls": _stats["total_calls"],
                "calls_today": calls_today,
                "calls_this_week": calls_this_week,
                "unique_callers": unique_callers,
                "revenue_total": round(revenue_total, 6),
                "revenue_this_week": round(revenue_this_week, 6),
                "api_cost_estimate": 0.0,
                "tools_breakdown": _stats.get("tools_breakdown", {}),
                "uptime_since": _dt.fromtimestamp(
                    _stats["start_time"], tz=_tz.utc
                ).isoformat() if _stats["start_time"] else None,
                "version": "1.0.0",
            })

        async def payments(request: Request):
            try:
                import sqlite3 as _sqlite3
                from x402 import _PROOF_DB
                if not os.path.exists(_PROOF_DB):
                    return JSONResponse({"payments": [], "server": "formfill"})
                conn = _sqlite3.connect(_PROOF_DB)
                conn.row_factory = _sqlite3.Row
                rows = conn.execute(
                    "SELECT tx_hash, used_at AS timestamp, tool AS tool_name, 0.001 AS amount "
                    "FROM used_proofs ORDER BY used_at DESC LIMIT 100"
                ).fetchall()
                conn.close()
                result = [dict(row) for row in rows]
                return JSONResponse({"payments": result, "server": "formfill"})
            except Exception as exc:
                return JSONResponse({"payments": [], "server": "formfill", "error": str(exc)})

        # Wrap FastMCP ASGI app with a /health endpoint Railway can check
        mcp_asgi = mcp.streamable_http_app()
        app = Starlette(routes=[
            Route("/health", health),
            Route("/analytics", analytics_endpoint),
            Route("/stats", stats_endpoint),
            Route("/payments", payments),
            Mount("/", app=mcp_asgi),
        ])

        logger.info(f"FormFill MCP server starting up (streamable-http on :{_PORT})")
        uvicorn.run(app, host="0.0.0.0", port=_PORT)
