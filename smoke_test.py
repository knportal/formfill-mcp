#!/usr/bin/env python3
"""
FormFill MCP — smoke test suite.

Run before every deploy to catch regressions:
    python smoke_test.py                        # basic (no network)
    python smoke_test.py --api-key ff_live_...  # full end-to-end via MCP tools
    python smoke_test.py --url https://formfill.plenitudo.ai  # against live server

Exit 0 = all passed. Exit 1 = one or more failures.
"""

import argparse
import base64
import io
import json
import os
import sys
import tempfile
import traceback

# ---------------------------------------------------------------------------
# Minimal fillable PDF (1 text field: test_name)
# Generated with pypdf — no external dependencies.
# ---------------------------------------------------------------------------
_MINIMAL_PDF_B64 = (
    "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+PgplbmRvYmoKMiAw"
    "IG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagoz"
    "IDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgovQWNyb0Zvcm0gPDwKL0ZpZWxk"
    "cyBbIDUgMCBSIF0KPj4KPj4KZW5kb2JqCjQgMCBvYmoKPDwKL1R5cGUgL1BhZ2UKL1Jlc291cmNl"
    "cyA8PAo+PgovTWVkaWFCb3ggWyAwLjAgMC4wIDYxMiA3OTIgXQovUGFyZW50IDIgMCBSCi9Bbm5v"
    "dHMgWyA1IDAgUiBdCj4+CmVuZG9iago1IDAgb2JqCjw8Ci9UeXBlIC9Bbm5vdAovU3VidHlwZSAv"
    "V2lkZ2V0Ci9GVCAvVHgKL1QgKHRlc3RcMTM3bmFtZSkKL1JlY3QgWyA3MiA3MDAgMzAwIDcyMCAg"
    "XQovViAoKQovREEgKFwwNTdIZWx2IDEyIFRmIDAgZykKPj4KZW5kb2JqCnhyZWYKMCA2CjAwMDAw"
    "MDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDU0IDAwMDAwIG4gCjAw"
    "MDAwMDAxMTMgMDAwMDAgbiAKMDAwMDAwMDE5NiAwMDAwMCBuIAowMDAwMDAwMzA4IDAwMDAwIG4g"
    "CnRyYWlsZXIKPDwKL1NpemUgNgovUm9vdCAzIDAgUgovSW5mbyAxIDAgUgo+PgpzdGFydHhyZWYK"
    "NDQxCiUlRU9GCg=="
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

results: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    try:
        msg = fn()
        results.append((name, True, msg or ""))
        print(f"  {PASS}  {name}" + (f" — {msg}" if msg else ""))
    except Exception as exc:
        results.append((name, False, str(exc)))
        print(f"  {FAIL}  {name} — {exc}")
        if os.getenv("SMOKE_VERBOSE"):
            traceback.print_exc()


def skip(name: str, reason: str):
    results.append((name, None, reason))
    print(f"  {SKIP}  {name} — {reason}")


# ---------------------------------------------------------------------------
# Unit checks (no server required)
# ---------------------------------------------------------------------------

def run_unit_checks():
    print("\n=== Unit checks ===")

    def check_pypdf():
        from pypdf import PdfReader, PdfWriter
        return "pypdf imported"

    def check_read_minimal():
        from pypdf import PdfReader
        data = base64.b64decode(_MINIMAL_PDF_B64)
        r = PdfReader(io.BytesIO(data))
        fields = r.get_fields() or {}
        assert "test_name" in fields, f"test_name missing, got: {list(fields.keys())}"
        return f"{len(fields)} field(s)"

    def check_fill_minimal():
        from pypdf import PdfReader, PdfWriter
        data = base64.b64decode(_MINIMAL_PDF_B64)
        r = PdfReader(io.BytesIO(data))
        w = PdfWriter()
        w.append(r)
        w.update_page_form_field_values(w.pages[0], {"test_name": "smoke_ok"}, auto_regenerate=False)
        buf = io.BytesIO()
        w.write(buf)
        r2 = PdfReader(io.BytesIO(buf.getvalue()))
        fields2 = r2.get_fields() or {}
        val = str(fields2.get("test_name", {}).get("/V", ""))
        assert val == "smoke_ok", f"expected 'smoke_ok', got '{val}'"
        return "fill+readback verified"

    def check_compat_acroform():
        sys.path.insert(0, os.path.dirname(__file__))
        from pypdf import PdfReader
        import server as _srv
        data = base64.b64decode(_MINIMAL_PDF_B64)
        r = PdfReader(io.BytesIO(data))
        info = _srv._pdf_compat_info(r)
        assert info.get("pdf_type") == "acroform", f"expected acroform, got {info}"
        return "pdf_type=acroform"

    def check_validate_fields():
        sys.path.insert(0, os.path.dirname(__file__))
        import server as _srv
        available = {"f1_01[0]": {}, "f1_02[0]": {}}
        valid, invalid = _srv._validate_fields(
            {"f1_01[0]": "Jane", "bad_field": "x"},
            available,
        )
        assert valid == {"f1_01[0]": "Jane"}
        assert invalid == ["bad_field"]
        return "valid/invalid split correct"

    def check_pdf_positions():
        from pypdf import PdfReader
        data = base64.b64decode(_MINIMAL_PDF_B64)
        r = PdfReader(io.BytesIO(data))
        page = r.pages[0]
        annots = page.get("/Annots") or []
        positions = []
        for ref in annots:
            obj = ref.get_object()
            if obj.get("/T"):
                rect = obj.get("/Rect")
                positions.append((str(obj["/T"]), float(rect[0]), float(rect[1])))
        assert positions, "no widget annotations found"
        return f"{len(positions)} widget(s) with positions"

    check("pypdf_import", check_pypdf)
    check("read_minimal_pdf", check_read_minimal)
    check("fill_and_readback", check_fill_minimal)
    check("compat_detection_acroform", check_compat_acroform)
    check("validate_fields_helper", check_validate_fields)
    check("widget_position_extraction", check_pdf_positions)


# ---------------------------------------------------------------------------
# Integration checks (require API key + local server import)
# ---------------------------------------------------------------------------

def run_integration_checks(api_key: str):
    print("\n=== Integration checks (local tools) ===")
    sys.path.insert(0, os.path.dirname(__file__))

    # Monkey-patch config so server imports cleanly without a running DB
    os.environ.setdefault("FORMFILL_DATA_DIR", tempfile.mkdtemp(prefix="formfill_smoke_"))

    import server as srv

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test_form.pdf")
        out = os.path.join(tmpdir, "test_form_filled.pdf")

        # Write minimal PDF to disk
        with open(src, "wb") as fh:
            fh.write(base64.b64decode(_MINIMAL_PDF_B64))

        def check_list_fields():
            result = json.loads(srv.list_form_fields(src, api_key=api_key))
            assert result["ok"], f"list_form_fields failed: {result}"
            assert "test_name" in str(result["fields"]), "test_name field not listed"
            fields = result["fields"]
            entry = next((v for k, v in fields.items() if "test_name" in k), None)
            assert entry is not None
            assert "position" in entry, "position missing from field entry"
            return f"{result['field_count']} field(s), positions included"

        def check_fill():
            result = json.loads(srv.fill_form(src, {"test_name": "Jane Doe"}, out, api_key=api_key))
            assert result["ok"], f"fill_form failed: {result}"
            assert result["fields_filled"] == 1
            assert os.path.exists(out), "output PDF not created"
            return f"fields_filled={result['fields_filled']}"

        def check_fill_readback():
            from pypdf import PdfReader
            r = PdfReader(out)
            fields = r.get_fields() or {}
            val = str(fields.get("test_name", {}).get("/V", ""))
            assert val == "Jane Doe", f"expected 'Jane Doe', got '{val}'"
            return f"value='{val}'"

        def check_fill_bad_fields():
            result = json.loads(srv.fill_form(src, {"nonexistent_field": "x"}, out + "_bad.pdf", api_key=api_key))
            assert not result["ok"], "expected ok:false for all-unknown fields"
            assert "unknown_fields" in result
            return "ok:false returned correctly"

        def check_extract():
            result = json.loads(srv.extract_form_data(out, api_key=api_key))
            assert result["ok"], f"extract_form_data failed: {result}"
            return f"{len(result.get('fields', {}))} field(s) extracted"

        check("list_form_fields", check_list_fields)
        check("fill_form", check_fill)
        check("fill_form_readback", check_fill_readback)
        check("fill_form_all_unknown_fields", check_fill_bad_fields)
        check("extract_form_data", check_extract)


# ---------------------------------------------------------------------------
# Live server checks (require --url)
# ---------------------------------------------------------------------------

def run_live_checks(base_url: str):
    print(f"\n=== Live server checks ({base_url}) ===")
    import urllib.request, urllib.error

    def check_health():
        with urllib.request.urlopen(f"{base_url}/health", timeout=10) as resp:
            data = json.loads(resp.read())
        assert data.get("status") == "ok", f"unexpected: {data}"
        return data.get("service", "")

    def check_smoke_endpoint():
        with urllib.request.urlopen(f"{base_url}/smoke-test", timeout=15) as resp:
            data = json.loads(resp.read())
        assert data.get("ok"), f"smoke-test failed: {data}"
        return f"checks={list(data.get('checks', {}).keys())}"

    check("GET /health", check_health)
    check("GET /smoke-test", check_smoke_endpoint)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FormFill MCP smoke tests")
    parser.add_argument("--api-key", help="API key for integration tests")
    parser.add_argument("--url", help="Base URL for live server tests (e.g. https://formfill.plenitudo.ai)")
    args = parser.parse_args()

    print("FormFill MCP — smoke test")
    print("=" * 40)

    run_unit_checks()

    if args.api_key:
        run_integration_checks(args.api_key)
    else:
        print("\n=== Integration checks (local tools) ===")
        print(f"  {SKIP}  (pass --api-key to run)")

    if args.url:
        run_live_checks(args.url.rstrip("/"))
    else:
        print("\n=== Live server checks ===")
        print(f"  {SKIP}  (pass --url to run)")

    # Summary
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    skipped = sum(1 for _, ok, _ in results if ok is None)

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")

    if failed:
        print(f"\nFailed checks:")
        for name, ok, msg in results:
            if ok is False:
                print(f"  - {name}: {msg}")
        sys.exit(1)

    print("All checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
