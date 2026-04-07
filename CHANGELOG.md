# Changelog

All notable changes to FormFill MCP are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2025-04-01

### Added

**Core tools**

- `list_form_fields` — Inspect any PDF and return every fillable field name, type, and current value. Use this before filling to discover available fields.
- `fill_form` — Fill a PDF form with provided field values and save the output to disk. Designed for standard single-page or short forms (under 5 pages).
- `fill_form_multipage` — Same as `fill_form`, but iterates page-by-page for reliability on large or complex documents (6+ pages, multi-section HR packets, tax bundles).
- `extract_form_data` — Extract all current field values from a filled PDF. Returns a field-name-to-value map. Useful for reading back a completed form.
- `flatten_form` — Convert an interactive PDF form into a non-editable flat PDF. Removes all form fields; values become static content permanently embedded in the document.

**Authentication & payments**

- API key authentication with per-key usage tracking (SQLite).
- Free tier: 50 form fills per month at no cost.
- Pro tier (Stripe): unlimited fills at $9.99/month via Stripe subscription.
- x402 micropayments: pay-per-use with USDC on Base — no account required. Pass a transaction hash as `payment_proof` in any tool call.
- Replay protection: each x402 transaction hash can only be used once.

**Infrastructure**

- One-click Railway deployment via included `railway.toml`.
- Cloudflare Worker proxy (`worker.js`) for routing remote agent traffic.
- `/health` endpoint for Railway health checks.
- Structured logging to file and stderr with configurable log level.
- Graceful field validation: unknown field names are reported in `warnings`, not silently dropped.

**Developer tooling**

- `manage_keys.py` CLI for creating, listing, and deactivating API keys.
- `stripe_webhook.py` for handling Stripe subscription events (upgrade, downgrade, cancellation).
- `.env.example` for local development setup.

### Notes

- PDF manipulation uses `pypdf`. Only interactive (AcroForm) PDFs are supported — flat/scanned PDFs are not fillable.
- The server exposes tools over the Model Context Protocol (MCP) using `fastmcp` with streamable HTTP transport.
- All tool responses are JSON strings with an `ok` boolean field for easy error handling.

---

## [Unreleased]

- Signature field support
- Batch fill across multiple forms
- Webhook notifications on fill completion
