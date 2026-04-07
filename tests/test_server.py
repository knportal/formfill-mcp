"""
Unit tests for FormFill MCP server tools.

These tests mock external dependencies (pypdf, auth, x402) so they can run
without a real PDF file, a database, or payment infrastructure.
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Lightweight stubs so server.py can be imported without its full dep tree
# ---------------------------------------------------------------------------

def _make_stub_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _setup_stubs():
    # config
    cfg = _make_stub_module("config")
    cfg.LOG_FILE = "/tmp/formfill_test.log"
    cfg.LOG_LEVEL = "ERROR"
    cfg.KEYS_DB = "/tmp/formfill_test_keys.db"

    # auth
    auth = _make_stub_module("auth")
    auth.validate_and_charge = MagicMock(return_value=(True, None))

    # x402
    x402 = _make_stub_module("x402")
    x402.PRICE_USDC = 0.01
    x402.WALLET_ADDRESS = "0xTestWallet"
    x402.is_proof_used = MagicMock(return_value=False)
    x402.mark_proof_used = MagicMock()
    x402.verify_payment = MagicMock(return_value=(True, None))
    x402.payment_required_response = MagicMock(
        return_value={"error": "Payment required", "x402": {"amount_usdc": 0.01}}
    )

    # pypdf — minimal fake
    pypdf = _make_stub_module("pypdf")

    class FakeField(dict):
        pass

    class FakePage:
        def get(self, key, default=None):  # noqa: D401
            return default

    class FakeReader:
        def __init__(self, path):
            self.pages = [FakePage(), FakePage()]

        def get_fields(self):
            return {
                "first_name": FakeField({"/FT": "/Tx", "/V": ""}),
                "last_name": FakeField({"/FT": "/Tx", "/V": ""}),
                "date": FakeField({"/FT": "/Tx", "/V": ""}),
            }

    class FakeWriter:
        def __init__(self):
            self.pages = []
            self._appended = False

        def append(self, reader):
            self.pages = list(reader.pages)
            self._appended = True

        def update_page_form_field_values(self, page, values):
            pass

        def add_page(self, page):
            self.pages.append(page)

        def write(self, fh):
            fh.write(b"%PDF-1.4 fake")

    pypdf.PdfReader = FakeReader
    pypdf.PdfWriter = FakeWriter

    # mcp
    mcp_pkg = _make_stub_module("mcp")
    mcp_server = _make_stub_module("mcp.server")
    mcp_fastmcp = _make_stub_module("mcp.server.fastmcp")

    class FakeMCP:
        def __init__(self, *a, **kw):
            pass

        def tool(self):
            def decorator(fn):
                return fn
            return decorator

    mcp_fastmcp.FastMCP = FakeMCP

    # pydantic
    pydantic = _make_stub_module("pydantic")
    pydantic.Field = MagicMock(return_value=None)


_setup_stubs()

# Now import the server module under test
import importlib
import os
os.environ.setdefault("PORT", "8000")

# We import the tool functions directly by loading the module source
# without running the __main__ block.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "formfill_server",
    str(Path(__file__).parent.parent / "server.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

list_form_fields = _mod.list_form_fields
fill_form = _mod.fill_form
fill_form_multipage = _mod.fill_form_multipage
extract_form_data = _mod.extract_form_data
flatten_form = _mod.flatten_form
_resolve = _mod._resolve
_validate_fields = _mod._validate_fields


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(response: str) -> dict:
    return json.loads(response)


class TempPDF:
    """Context manager that creates a minimal fake PDF on disk."""

    def __enter__(self):
        import tempfile
        self._f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self._f.write(b"%PDF-1.4 placeholder")
        self._f.flush()
        self._f.close()
        self.path = self._f.name
        return self.path

    def __exit__(self, *_):
        os.unlink(self.path)


class TempOutputPDF:
    """Returns a path inside a temp dir that doesn't exist yet."""

    def __enter__(self):
        import tempfile
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, "output.pdf")
        return self.path

    def __exit__(self, *_):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests — _resolve
# ---------------------------------------------------------------------------

class TestResolve(unittest.TestCase):

    def test_missing_file_returns_error(self):
        path, err = _resolve("/nonexistent/path/file.pdf")
        self.assertIsNone(path)
        self.assertIn("not found", err.lower())

    def test_existing_file_returns_path(self):
        with TempPDF() as pdf_path:
            path, err = _resolve(pdf_path)
            self.assertIsNone(err)
            self.assertEqual(str(path), pdf_path)


# ---------------------------------------------------------------------------
# Tests — _validate_fields
# ---------------------------------------------------------------------------

class TestValidateFields(unittest.TestCase):

    def test_all_valid(self):
        requested = {"name": "Alice", "date": "2025-01-01"}
        available = {"name": None, "date": None, "ssn": None}
        valid, invalid = _validate_fields(requested, available)
        self.assertEqual(valid, requested)
        self.assertEqual(invalid, [])

    def test_some_invalid(self):
        requested = {"name": "Alice", "unknown_field": "value"}
        available = {"name": None}
        valid, invalid = _validate_fields(requested, available)
        self.assertEqual(valid, {"name": "Alice"})
        self.assertEqual(invalid, ["unknown_field"])

    def test_all_invalid(self):
        requested = {"bad_field": "x"}
        available = {"real_field": None}
        valid, invalid = _validate_fields(requested, available)
        self.assertEqual(valid, {})
        self.assertEqual(len(invalid), 1)


# ---------------------------------------------------------------------------
# Tests — list_form_fields
# ---------------------------------------------------------------------------

class TestListFormFields(unittest.TestCase):

    def test_missing_auth_returns_error(self):
        result = _ok(list_form_fields(pdf_path="/any.pdf"))
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)

    def test_valid_api_key_returns_fields(self):
        with TempPDF() as pdf_path:
            result = _ok(list_form_fields(pdf_path=pdf_path, api_key="ff_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertIn("fields", result)
        self.assertGreater(result.get("field_count", 0), 0)

    def test_nonexistent_pdf_returns_error(self):
        result = _ok(list_form_fields(
            pdf_path="/no/such/file.pdf",
            api_key="ff_free_test",
        ))
        self.assertFalse(result.get("ok"))

    def test_response_includes_field_types(self):
        with TempPDF() as pdf_path:
            result = _ok(list_form_fields(pdf_path=pdf_path, api_key="ff_free_test"))
        for field_data in result.get("fields", {}).values():
            self.assertIn("type", field_data)
            self.assertIn("current_value", field_data)


# ---------------------------------------------------------------------------
# Tests — fill_form
# ---------------------------------------------------------------------------

class TestFillForm(unittest.TestCase):

    def test_missing_auth_returns_payment_required(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form(
                pdf_path=src,
                field_values={"first_name": "Alice"},
                output_path=dst,
            ))
        self.assertFalse(result.get("ok"))

    def test_fill_with_valid_api_key(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form(
                pdf_path=src,
                field_values={"first_name": "Alice", "last_name": "Smith"},
                output_path=dst,
                api_key="ff_free_test",
            ))
        self.assertTrue(result.get("ok"))
        self.assertIn("output_path", result)
        self.assertIn("fields_filled", result)

    def test_unknown_fields_reported_in_warnings(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form(
                pdf_path=src,
                field_values={"first_name": "Alice", "nonexistent_field": "X"},
                output_path=dst,
                api_key="ff_free_test",
            ))
        self.assertTrue(result.get("ok"))
        self.assertIn("warnings", result)
        self.assertIn("nonexistent_field", result["warnings"]["unknown_fields"])

    def test_nonexistent_pdf_returns_error(self):
        result = _ok(fill_form(
            pdf_path="/no/such/file.pdf",
            field_values={"name": "Alice"},
            output_path="/tmp/out.pdf",
            api_key="ff_free_test",
        ))
        self.assertFalse(result.get("ok"))

    def test_x402_payment_proof_accepted(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form(
                pdf_path=src,
                field_values={"first_name": "Bob"},
                output_path=dst,
                payment_proof="0xabc123fakehash",
            ))
        self.assertTrue(result.get("ok"))

    def test_x402_replay_rejected(self):
        sys.modules["x402"].is_proof_used = MagicMock(return_value=True)
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form(
                pdf_path=src,
                field_values={"first_name": "Bob"},
                output_path=dst,
                payment_proof="0xabc123fakehash",
            ))
        self.assertFalse(result.get("ok"))
        self.assertIn("already used", result.get("error", ""))
        # Restore
        sys.modules["x402"].is_proof_used = MagicMock(return_value=False)


# ---------------------------------------------------------------------------
# Tests — fill_form_multipage
# ---------------------------------------------------------------------------

class TestFillFormMultipage(unittest.TestCase):

    def test_fill_multipage_returns_pages_updated(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form_multipage(
                pdf_path=src,
                field_values={"first_name": "Carol", "last_name": "Jones"},
                output_path=dst,
                api_key="ff_free_test",
            ))
        self.assertTrue(result.get("ok"))
        self.assertIn("pages_updated", result)
        self.assertIsInstance(result["pages_updated"], list)

    def test_fill_multipage_missing_auth(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form_multipage(
                pdf_path=src,
                field_values={"first_name": "Carol"},
                output_path=dst,
            ))
        self.assertFalse(result.get("ok"))


# ---------------------------------------------------------------------------
# Tests — extract_form_data
# ---------------------------------------------------------------------------

class TestExtractFormData(unittest.TestCase):

    def test_extract_returns_ok_with_api_key(self):
        with TempPDF() as src:
            result = _ok(extract_form_data(
                pdf_path=src,
                api_key="ff_free_test",
            ))
        # The fake PDF has no real annotations, so field_count may be 0 — that's fine
        self.assertTrue(result.get("ok"))
        self.assertIn("fields", result)

    def test_extract_missing_auth(self):
        with TempPDF() as src:
            result = _ok(extract_form_data(pdf_path=src))
        self.assertFalse(result.get("ok"))


# ---------------------------------------------------------------------------
# Tests — flatten_form
# ---------------------------------------------------------------------------

class TestFlattenForm(unittest.TestCase):

    def test_flatten_with_api_key(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(flatten_form(
                pdf_path=src,
                output_path=dst,
                api_key="ff_free_test",
            ))
        self.assertTrue(result.get("ok"))
        self.assertIn("output_path", result)
        self.assertIn("pages", result)

    def test_flatten_missing_auth(self):
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(flatten_form(pdf_path=src, output_path=dst))
        self.assertFalse(result.get("ok"))

    def test_flatten_nonexistent_source(self):
        result = _ok(flatten_form(
            pdf_path="/no/such/file.pdf",
            output_path="/tmp/flat.pdf",
            api_key="ff_free_test",
        ))
        self.assertFalse(result.get("ok"))


# ---------------------------------------------------------------------------
# Tests — auth error format
# ---------------------------------------------------------------------------

class TestAuthErrorFormat(unittest.TestCase):

    def test_invalid_api_key_shape(self):
        """All auth errors must include ok=false and an error field."""
        sys.modules["auth"].validate_and_charge = MagicMock(
            return_value=(False, "Invalid API key")
        )
        with TempPDF() as src, TempOutputPDF() as dst:
            result = _ok(fill_form(
                pdf_path=src,
                field_values={},
                output_path=dst,
                api_key="ff_free_bad_key",
            ))
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)
        # Restore
        sys.modules["auth"].validate_and_charge = MagicMock(return_value=(True, None))


if __name__ == "__main__":
    unittest.main()
