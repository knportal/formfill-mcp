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


def _authenticate(
    api_key: str | None,
    payment_proof: str | None,
    tool_name: str,
) -> tuple[bool, str | None, bool, float]:
    """Validate auth for a tool call.

    Returns (authorized, error_json, paid, amount_usdc).
    If authorized is False, return error_json immediately to the caller.
    """
    if api_key:
        ok, err = validate_and_charge(api_key)
        if not ok:
            return False, _auth_error(err), False, 0.0
        return True, None, False, 0.0
    elif payment_proof:
        if is_proof_used(payment_proof):
            return False, json.dumps({"ok": False, "error": "Payment proof already used"}), False, 0.0
        ok, err = verify_payment(payment_proof, PRICE_USDC, WALLET_ADDRESS)
        if not ok:
            return False, json.dumps({"ok": False, "error": f"Payment verification failed: {err}"}), False, 0.0
        mark_proof_used(payment_proof, tool_name)
        return True, None, True, PRICE_USDC
    else:
        return False, json.dumps(payment_required_response(tool_name)), False, 0.0


def _check_admin(request) -> bool:
    """Return True if the request carries a valid admin secret.

    Checks Authorization: Bearer <ADMIN_SECRET>.
    If ADMIN_SECRET is not configured, all requests are allowed.
    """
    from config import ADMIN_SECRET
    if not ADMIN_SECRET:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {ADMIN_SECRET}"


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


def _pdf_compat_info(reader: "PdfReader") -> dict:
    """Return PDF compatibility metadata: type, XFA presence, encryption.

    pdf_type values:
      "acroform"           — standard fillable PDF (fully supported)
      "hybrid_xfa_acroform"— IRS/government forms; filled via AcroForm but
                             visual rendering depends on viewer XFA support
      "xfa_only"           — XFA-only; cannot be filled programmatically
      "flat"               — no form fields at all

    Callers should surface xfa_note and any warnings to the user.
    """
    info: dict = {}

    if reader.is_encrypted:
        info["encrypted"] = True
        info["warning"] = (
            "PDF is password-protected. Decrypt it first before filling."
        )
        info["pdf_type"] = "encrypted"
        return info

    try:
        catalog = reader.trailer["/Root"].get_object()
        acroform_obj = catalog.get("/AcroForm")
        if acroform_obj is None:
            info["pdf_type"] = "flat"
            return info
        acroform = acroform_obj.get_object()
        xfa = acroform.get("/XFA")
        has_acroform_fields = bool(acroform.get("/Fields"))
        if xfa and has_acroform_fields:
            info["pdf_type"] = "hybrid_xfa_acroform"
            info["xfa_note"] = (
                "This PDF uses both XFA and AcroForm (common in IRS/government forms). "
                "Fields are filled via AcroForm. Always use the exact field names AND "
                "position coordinates returned by list_form_fields — field names like "
                "f1_07[0] give no visual hint; the position (x, y) tells you where the "
                "field actually appears on the page."
            )
        elif xfa:
            info["pdf_type"] = "xfa_only"
            info["xfa_note"] = (
                "This PDF uses XFA only and cannot be filled programmatically. "
                "Open it in Adobe Acrobat or a compatible viewer."
            )
        else:
            info["pdf_type"] = "acroform"
    except Exception:
        info["pdf_type"] = "unknown"

    return info


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
    """Inspect a PDF and return every fillable field: name, type, current value, and x/y position on the page.

    ALWAYS call this before fill_form. Use the exact field names returned here — never guess.
    Use the position coordinates (x, y) to identify what each field represents visually:
    higher y = higher on the page in PDF coordinates. Fields with similar y values are on
    the same horizontal line; fields with similar x values are in the same column.
    The response also includes pdf_type so you know if the PDF may have rendering quirks."""
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
        compat = _pdf_compat_info(reader)

        if compat.get("pdf_type") == "encrypted":
            _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, **compat})

        if compat.get("pdf_type") == "xfa_only":
            _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, **compat})

        fields = _get_reader_fields(reader)

        if not fields:
            _log_call("list_form_fields", bool(payment_proof), 0.0, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({
                "ok": False,
                "pdf_type": compat.get("pdf_type", "flat"),
                "error": "No fillable fields found in this PDF.",
                "note": (
                    "The PDF may be flat/scanned rather than an interactive form. "
                    "Try opening it in Acrobat to confirm."
                ),
            })

        # Build name→position map from widget annotations so Claude can
        # identify fields by their visual location on the page.
        widget_positions: dict[str, dict] = {}
        for page_num, page in enumerate(reader.pages):
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot_ref in annots:
                annot = annot_ref.get_object()
                if annot.get("/Subtype") != "/Widget":
                    continue
                t = annot.get("/T")
                if not t:
                    continue
                rect = annot.get("/Rect")
                if rect:
                    widget_positions[str(t)] = {
                        "page": page_num + 1,
                        "x": round(float(rect[0])),
                        "y": round(float(rect[1])),
                    }

        field_info = {}
        for name, field in fields.items():
            raw_type = str(field.get("/FT", "unknown"))
            type_map = {
                "/Tx": "text",
                "/Btn": "button/checkbox",
                "/Ch": "choice/dropdown",
                "/Sig": "signature",
            }
            entry: dict = {
                "type": type_map.get(raw_type, raw_type),
                "current_value": str(field.get("/V", "")),
            }
            leaf = name.split(".")[-1] if "." in name else name
            if leaf in widget_positions:
                entry["position"] = widget_positions[leaf]
            field_info[name] = entry

        logger.info("list_form_fields: %s — %d fields (%s)", src.name, len(field_info), compat.get("pdf_type"))
        _log_call("list_form_fields", bool(payment_proof), 0.0, True, int((_time.monotonic() - _t0) * 1000))
        response: dict = {
            "ok": True,
            "pdf_type": compat.get("pdf_type", "acroform"),
            "field_count": len(field_info),
            "fields": field_info,
        }
        if "xfa_note" in compat:
            response["xfa_note"] = compat["xfa_note"]
        return json.dumps(response, indent=2)

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
    """Fill a PDF form with the given field values and save the result to disk.

    WORKFLOW: 1) Call list_form_fields first to get exact field names and their x/y positions.
    2) Use position coordinates to confirm which field is which — higher y = higher on page.
    3) Pass exact field names from list_form_fields here. Never guess field names.

    Use for single-page or short forms (under 5 pages). Use fill_form_multipage for longer forms.

    Returns ok:false with unknown_fields if ALL provided field names are invalid.
    Returns ok:true with a warnings.unknown_fields list if SOME names are invalid (partial fill)."""
    _stats["total_calls"] += 1
    _track_tool("fill_form")
    _t0 = _time.monotonic()
    _auth_ok, _auth_err, _paid, _amount = _authenticate(api_key, payment_proof, "fill_form")
    if not _auth_ok:
        _log_call("fill_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_err

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
        compat = _pdf_compat_info(reader)

        if compat.get("pdf_type") in ("encrypted", "xfa_only"):
            _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, **compat})

        available_fields = _get_reader_fields(reader)

        if not available_fields:
            _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({
                "ok": False,
                "pdf_type": compat.get("pdf_type", "flat"),
                "error": "No fillable fields found in this PDF.",
            })

        # Validate requested field names
        valid_values, invalid_names = _validate_fields(field_values, available_fields)

        # Fail hard if every field name is wrong — likely using wrong names
        if invalid_names and not valid_values:
            _log_call("fill_form", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({
                "ok": False,
                "error": "None of the provided field names exist in this PDF. Call list_form_fields to get the correct names.",
                "unknown_fields": invalid_names,
                "valid_fields": list(available_fields.keys()),
            })

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
            writer.update_page_form_field_values(page, field_values, auto_regenerate=False)

        with open(str(dst), "wb") as fh:
            writer.write(fh)

        result: dict = {
            "ok": True,
            "output_path": str(dst),
            "pdf_type": compat.get("pdf_type", "acroform"),
            "fields_filled": len(valid_values),
            "pages": len(reader.pages),
            "message": f"Filled PDF saved to {dst}",
        }
        if invalid_names:
            result["warnings"] = {
                "unknown_fields": invalid_names,
                "hint": "Call list_form_fields to get correct field names and their positions.",
                "valid_fields": list(available_fields.keys()),
            }
        if "xfa_note" in compat:
            result["xfa_note"] = compat["xfa_note"]

        logger.info(
            "fill_form: %s → %s (%d/%d fields, %d pages)",
            src.name,
            dst.name,
            len(valid_values),
            len(field_values),
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
    """Fill a multi-page PDF form, iterating page-by-page for reliability.

    WORKFLOW: 1) Call list_form_fields first to get exact field names and their x/y positions.
    2) Use position coordinates to confirm which field is which — higher y = higher on page.
    3) Pass exact field names from list_form_fields here. Never guess field names.

    Use when the PDF has more than 5 pages or fields spanning multiple pages (rental applications,
    tax packets, multi-section HR forms). Prefer this over fill_form for any complex/long document.

    Returns ok:false with unknown_fields if ALL provided field names are invalid.
    Returns ok:true with a warnings.unknown_fields list if SOME names are invalid (partial fill)."""
    _stats["total_calls"] += 1
    _track_tool("fill_form_multipage")
    _t0 = _time.monotonic()
    _auth_ok, _auth_err, _paid, _amount = _authenticate(api_key, payment_proof, "fill_form_multipage")
    if not _auth_ok:
        _log_call("fill_form_multipage", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_err

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
        compat = _pdf_compat_info(reader)

        if compat.get("pdf_type") in ("encrypted", "xfa_only"):
            _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({"ok": False, **compat})

        available_fields = _get_reader_fields(reader)

        if not available_fields:
            _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({
                "ok": False,
                "pdf_type": compat.get("pdf_type", "flat"),
                "error": "No fillable fields found in this PDF.",
            })

        valid_values, invalid_names = _validate_fields(field_values, available_fields)

        if invalid_names and not valid_values:
            _log_call("fill_form_multipage", _paid, _amount, False, int((_time.monotonic() - _t0) * 1000))
            return json.dumps({
                "ok": False,
                "error": "None of the provided field names exist in this PDF. Call list_form_fields to get the correct names.",
                "unknown_fields": invalid_names,
                "valid_fields": list(available_fields.keys()),
            })

        if invalid_names:
            logger.warning(
                "fill_form_multipage: %d unknown field(s) for %s: %s",
                len(invalid_names),
                src.name,
                invalid_names,
            )

        writer = PdfWriter()
        writer.append(reader)

        pages_updated = []
        for i, page in enumerate(writer.pages):
            writer.update_page_form_field_values(page, field_values, auto_regenerate=False)
            pages_updated.append(i + 1)

        with open(str(dst), "wb") as fh:
            writer.write(fh)

        result: dict = {
            "ok": True,
            "output_path": str(dst),
            "pdf_type": compat.get("pdf_type", "acroform"),
            "fields_filled": len(valid_values),
            "total_pages": len(reader.pages),
            "pages_updated": pages_updated,
            "message": f"Multi-page filled PDF saved to {dst}",
        }
        if invalid_names:
            result["warnings"] = {
                "unknown_fields": invalid_names,
                "hint": "Call list_form_fields to get correct field names and their positions.",
                "valid_fields": list(available_fields.keys()),
            }
        if "xfa_note" in compat:
            result["xfa_note"] = compat["xfa_note"]

        logger.info(
            "fill_form_multipage: %s → %s (%d/%d fields, %d pages)",
            src.name,
            dst.name,
            len(valid_values),
            len(field_values),
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
    _auth_ok, _auth_err, _paid, _amount = _authenticate(api_key, payment_proof, "extract_form_data")
    if not _auth_ok:
        _log_call("extract_form_data", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_err

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
    _auth_ok, _auth_err, _paid, _amount = _authenticate(api_key, payment_proof, "flatten_form")
    if not _auth_ok:
        _log_call("flatten_form", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _auth_err

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
        from contextlib import asynccontextmanager
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        async def health(request: Request):
            return JSONResponse({"status": "ok", "service": "formfill-mcp"})

        async def smoke_test(request: Request):
            """GET /smoke-test — fills a minimal in-memory PDF and verifies the result.
            Returns {"ok": true, "checks": {...}} or {"ok": false, "failed": [...]}."""
            import base64, io, time as _t
            checks: dict = {}
            failed: list = []

            # 1. pypdf available
            if _PYPDF_OK:
                checks["pypdf"] = "ok"
            else:
                checks["pypdf"] = "missing"
                failed.append("pypdf not installed")

            # 2. Auth DB readable
            try:
                from config import KEYS_DB
                conn = _sqlite3.connect(KEYS_DB)
                conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()
                conn.close()
                checks["auth_db"] = "ok"
            except Exception as exc:
                checks["auth_db"] = str(exc)
                failed.append(f"auth_db: {exc}")

            # 3. Analytics DB readable
            try:
                conn = _sqlite3.connect(_ANALYTICS_DB)
                conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()
                conn.close()
                checks["analytics_db"] = "ok"
            except Exception as exc:
                checks["analytics_db"] = str(exc)
                failed.append(f"analytics_db: {exc}")

            # 4. Fill a minimal in-memory AcroForm PDF and read it back
            if _PYPDF_OK:
                try:
                    _MINIMAL_PDF_B64 = (
                        "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAw"
                        "IG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagoz"
                        "IDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgovQWNyb0Zvcm0gPDwKL0ZpZWxk"
                        "cyBbIDUgMCBSIF0KPj4KPj4KZW5kb2JqCjQgMCBvYmoKPDwKL1R5cGUgL1BhZ2UKL1Jlc291cmNl"
                        "cyA8PAo+PgovTWVkaWFCb3ggWyAwLjAgMC4wIDYxMiA3OTIgXQovUGFyZW50IDIgMCBSCi9Bbm5v"
                        "dHMgWyA1IDAgUiBdCj4+CmVuZG9iago1IDAgb2JqCjw8Ci9UeXBlIC9Bbm5vdAovU3VidHlwZSAv"
                        "V2lkZ2V0Ci9GVCAvVHgKL1QgKHRlc3RcMTM3bmFtZSkKL1JlY3QgWyA3MiA3MDAgMzAwIDcyMCBd"
                        "Ci9WICgpCi9EQSAoXDA1N0hlbHYgMTIgVGYgMCBnKQo+PgplbmRvYmoKeHJlZgowIDYKMDAwMDAw"
                        "MDAwMCA2NTUzNSBmIAowMDAwMDAwMDE1IDAwMDAwIG4gCjAwMDAwMDAwNTQgMDAwMDAgbiAKMDAwMDAw"
                        "MDExMyAwMDAwMCBuIAowMDAwMDAwMTk2IDAwMDAwIG4gCjAwMDAwMDAzMDggMDAwMDAgbiAKdHJhaWxl"
                        "cgo8PAovU2l6ZSA2Ci9Sb290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgo0NDEKJSVF"
                        "T0YK"
                    )
                    pdf_bytes = base64.b64decode(_MINIMAL_PDF_B64)
                    reader = PdfReader(io.BytesIO(pdf_bytes))
                    fields = reader.get_fields() or {}
                    if "test_name" not in fields:
                        raise ValueError(f"Expected test_name field, got: {list(fields.keys())}")
                    writer = PdfWriter()
                    writer.append(reader)
                    writer.update_page_form_field_values(
                        writer.pages[0], {"test_name": "smoke_ok"}, auto_regenerate=False
                    )
                    buf = io.BytesIO()
                    writer.write(buf)
                    reader2 = PdfReader(io.BytesIO(buf.getvalue()))
                    filled = reader2.get_fields() or {}
                    val = str(filled.get("test_name", {}).get("/V", ""))
                    if val != "smoke_ok":
                        raise ValueError(f"Fill verification failed: got '{val}'")
                    checks["pdf_fill"] = "ok"
                except Exception as exc:
                    checks["pdf_fill"] = str(exc)
                    failed.append(f"pdf_fill: {exc}")

            status = 200 if not failed else 500
            return JSONResponse(
                {"ok": not failed, "checks": checks, "failed": failed},
                status_code=status,
            )

        async def analytics_endpoint(request: Request):
            """
            GET /analytics — returns standardised call analytics from analytics.db.
            Schema:
              total_calls, paid_calls, total_revenue_usdc,
              calls_by_tool, avg_latency_ms, last_24h_calls
            """
            if not _check_admin(request):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
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
            if not _check_admin(request):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
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
            if not _check_admin(request):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
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

        # ---------------------------------------------------------------------------
        # Stripe webhook — integrated into main Starlette app (replaces port-8090 Flask)
        # ---------------------------------------------------------------------------
        async def stripe_webhook_handler(request: Request):
            import stripe as _stripe
            from config import STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
            from auth import set_key_tier

            if not STRIPE_WEBHOOK_SECRET:
                logger.error("STRIPE_WEBHOOK_SECRET not configured")
                return JSONResponse({"error": "Webhook not configured"}, status_code=500)

            _stripe.api_key = STRIPE_SECRET_KEY
            payload = await request.body()
            sig_header = request.headers.get("stripe-signature", "")

            try:
                event = _stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
            except ValueError:
                return JSONResponse({"error": "Invalid payload"}, status_code=400)
            except _stripe.error.SignatureVerificationError:
                return JSONResponse({"error": "Invalid signature"}, status_code=400)

            event_type = event["type"]
            data = event["data"]["object"]
            logger.info("Stripe event: %s id=%s", event_type, event["id"])

            customer_id = data.get("customer")
            api_key = data.get("metadata", {}).get("formfill_api_key")
            if not api_key and customer_id:
                try:
                    customer = _stripe.Customer.retrieve(customer_id)
                    api_key = customer.get("metadata", {}).get("formfill_api_key")
                except Exception as exc:
                    logger.error("Failed to retrieve Stripe customer %s: %s", customer_id, exc)

            if event_type == "customer.subscription.created" and api_key:
                set_key_tier(api_key, "pro", stripe_customer=customer_id)
                logger.info("Upgraded key %s… to pro", api_key[:16])
            elif event_type == "customer.subscription.deleted" and api_key:
                set_key_tier(api_key, "free", stripe_customer=customer_id)
                logger.info("Downgraded key %s… to free", api_key[:16])

            return JSONResponse({"ok": True})

        # ---------------------------------------------------------------------------
        # POST /api/signup — issue a free API key (no payment needed)
        # ---------------------------------------------------------------------------
        async def api_signup(request: Request):
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

            email = (body.get("email") or "").strip()
            if not email or "@" not in email:
                return JSONResponse({"error": "Valid email is required"}, status_code=400)

            from auth import create_key
            from config import FREE_MONTHLY_LIMIT
            api_key = create_key(tier="free")
            logger.info("Free key issued email=%s key=%s…", email[:30], api_key[:16])

            return JSONResponse({
                "api_key": api_key,
                "tier": "free",
                "monthly_limit": FREE_MONTHLY_LIMIT,
                "message": f"Your free key gives you {FREE_MONTHLY_LIMIT} fills/month. "
                           "Pass it as the api_key parameter to any FormFill tool.",
                "upgrade_url": "https://formfill.plenitudo.ai/upgrade",
            })

        # ---------------------------------------------------------------------------
        # POST /api/checkout — create a Stripe checkout session to upgrade to Pro
        # ---------------------------------------------------------------------------
        async def api_checkout(request: Request):
            import stripe as _stripe
            from config import STRIPE_SECRET_KEY, STRIPE_PRO_PRICE_ID

            if not STRIPE_SECRET_KEY or not STRIPE_PRO_PRICE_ID:
                return JSONResponse({"error": "Stripe not configured on this server"}, status_code=503)

            _stripe.api_key = STRIPE_SECRET_KEY

            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

            api_key = (body.get("api_key") or "").strip()
            email = (body.get("email") or "").strip()

            if not api_key:
                return JSONResponse({"error": "api_key is required"}, status_code=400)

            try:
                # Dedupe: passing customer_email makes Checkout create a brand-new
                # customer every time, so a returning email ends up double-billed.
                # Reuse the existing customer record instead, and refuse checkout
                # outright if any record for this email already has an active Pro
                # subscription. An email can map to multiple customer records
                # (pre-dedupe history), so scan them all.
                existing_customer_id = None
                if email:
                    for cust in _stripe.Customer.list(email=email, limit=10).data:
                        if existing_customer_id is None:
                            existing_customer_id = cust.id
                        subs = _stripe.Subscription.list(
                            customer=cust.id, status="active", limit=10
                        ).data
                        for sub in subs:
                            for item in sub["items"]["data"]:
                                if item["price"]["id"] == STRIPE_PRO_PRICE_ID:
                                    return JSONResponse(
                                        {
                                            "error": "This email already has an active "
                                            "FormFill Pro subscription. Manage it from "
                                            "the billing portal instead of subscribing again."
                                        },
                                        status_code=409,
                                    )

                checkout_kwargs = {
                    "mode": "subscription",
                    "line_items": [{"price": STRIPE_PRO_PRICE_ID, "quantity": 1}],
                    "metadata": {"formfill_api_key": api_key},
                    "success_url": "https://formfill.plenitudo.ai/success?session_id={CHECKOUT_SESSION_ID}",
                    "cancel_url": "https://formfill.plenitudo.ai/pricing",
                }
                if existing_customer_id:
                    checkout_kwargs["customer"] = existing_customer_id
                elif email:
                    checkout_kwargs["customer_email"] = email

                session = _stripe.checkout.Session.create(**checkout_kwargs)
            except Exception as exc:
                logger.error("Stripe checkout creation failed: %s", exc)
                return JSONResponse({"error": "Failed to create checkout session"}, status_code=500)

            return JSONResponse({"checkout_url": session.url})

        # ---------------------------------------------------------------------------
        # GET /api/key-info?api_key=ff_free_...
        # ---------------------------------------------------------------------------
        async def api_key_info(request: Request):
            from auth import get_usage
            from config import FREE_MONTHLY_LIMIT, KEYS_DB
            import sqlite3 as _sq

            api_key = request.query_params.get("api_key", "").strip()
            if not api_key:
                return JSONResponse({"error": "api_key query parameter required"}, status_code=400)

            try:
                conn = _sq.connect(KEYS_DB)
                conn.row_factory = _sq.Row
                row = conn.execute(
                    "SELECT tier, active, created_at FROM api_keys WHERE key = ?", (api_key,)
                ).fetchone()
                conn.close()
            except Exception:
                return JSONResponse({"error": "Database error"}, status_code=500)

            if row is None:
                return JSONResponse({"error": "API key not found"}, status_code=404)
            if not row["active"]:
                return JSONResponse({"error": "API key is deactivated"}, status_code=403)

            usage = get_usage(api_key)
            tier = row["tier"]

            return JSONResponse({
                "api_key": api_key[:16] + "…",
                "tier": tier,
                "created_at": row["created_at"],
                "monthly_limit": None if tier == "pro" else FREE_MONTHLY_LIMIT,
                "current_month_fills": usage["current_month_fills"],
                "total_fills": usage["total_fills"],
            })

        # GET /api/billing?api_key=ff_... — redirect to Stripe customer portal
        # ---------------------------------------------------------------------------
        async def api_billing(request: Request):
            import stripe as _stripe
            from config import STRIPE_SECRET_KEY
            from starlette.responses import RedirectResponse

            if not STRIPE_SECRET_KEY:
                return JSONResponse({"error": "Stripe not configured"}, status_code=503)

            api_key = (request.query_params.get("api_key") or "").strip()
            if not api_key:
                return JSONResponse({"error": "api_key query parameter is required"}, status_code=400)

            # Look up the Stripe customer ID for this key
            try:
                from config import KEYS_DB
                conn = _sqlite3.connect(KEYS_DB)
                conn.row_factory = _sqlite3.Row
                row = conn.execute(
                    "SELECT stripe_customer FROM api_keys WHERE key = ? AND active = 1",
                    (api_key,)
                ).fetchone()
                conn.close()
            except Exception as exc:
                return JSONResponse({"error": f"Database error: {exc}"}, status_code=500)

            if row is None:
                return JSONResponse({"error": "API key not found or inactive"}, status_code=404)

            stripe_customer_id = row["stripe_customer"] if row["stripe_customer"] else None

            if not stripe_customer_id:
                return JSONResponse({
                    "error": "No subscription found for this API key. Upgrade at https://formfill.plenitudo.ai"
                }, status_code=404)

            try:
                _stripe.api_key = STRIPE_SECRET_KEY
                session = _stripe.billing_portal.Session.create(
                    customer=stripe_customer_id,
                    return_url="https://formfill.plenitudo.ai",
                )
                return RedirectResponse(url=session.url, status_code=303)
            except Exception as exc:
                logger.error("Billing portal session failed: %s", exc)
                return JSONResponse({"error": "Failed to create billing session"}, status_code=500)

        # Wrap FastMCP ASGI app with a /health endpoint Railway can check.
        # The inner MCP app has its own lifespan (starts the session manager task
        # group). Starlette doesn't propagate sub-app lifespans, so we drive it
        # explicitly from the outer app's lifespan.
        mcp_asgi = mcp.streamable_http_app()

        @asynccontextmanager
        async def lifespan(app):
            async with mcp_asgi.router.lifespan_context(mcp_asgi):
                yield

        app = Starlette(
            lifespan=lifespan,
            routes=[
                Route("/health", health),
                Route("/smoke-test", smoke_test),
                Route("/analytics", analytics_endpoint),
                Route("/stats", stats_endpoint),
                Route("/payments", payments),
                Route("/webhook/stripe", stripe_webhook_handler, methods=["POST"]),
                Route("/stripe-webhook", stripe_webhook_handler, methods=["POST"]),
                Route("/api/signup", api_signup, methods=["POST"]),
                Route("/api/checkout", api_checkout, methods=["POST"]),
                Route("/api/key-info", api_key_info, methods=["GET"]),
                Route("/api/billing", api_billing, methods=["GET"]),
                Mount("/", app=mcp_asgi),
            ],
        )

        logger.info(f"FormFill MCP server starting up (streamable-http on :{_PORT})")
        uvicorn.run(app, host="0.0.0.0", port=_PORT)
