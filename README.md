<div align="center">
  <img src="assets/logo.png" alt="FormFill MCP" width="120" />
  <h1>FormFill MCP</h1>
  <p><strong>Fill any PDF form from your AI agent — in a single tool call.</strong></p>

  [![smithery badge](https://smithery.ai/badge/@knportal/formfill-mcp)](https://smithery.ai/server/@knportal/formfill-mcp)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
  [![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](smithery.yaml)
  [![MCP](https://img.shields.io/badge/MCP-compatible-brightgreen.svg)](https://modelcontextprotocol.io)

  <p>Tax forms · HR paperwork · Legal documents · Lease agreements · Insurance claims</p>
  <p>If it has fillable fields, FormFill can fill it.</p>

  **[Get API Key](https://formfill.plenitudo.ai?ref=readme)** · **[View on Smithery](https://smithery.ai/server/@knportal/formfill-mcp)** · Built by [Plenitudo AI](https://plenitudo.ai)

  **Listed on:** [Smithery](https://smithery.ai/server/formfill-mcp?ref=readme) · [Glama](https://glama.ai/mcp/servers/knportal/formfill-mcp?ref=readme)
</div>

---


## How It Works

```
 ┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
 │  1. Inspect     │        │  2. Fill         │        │  3. Done        │
 │                 │   →    │                 │   →    │                 │
 │  list_form_     │        │  fill_form       │        │  Filled PDF     │
 │  fields         │        │                 │        │  saved to disk  │
 │                 │        │                 │        │                 │
 │  Returns every  │        │  Writes values, │        │  Open in        │
 │  field name,    │        │  saves output   │        │  Preview or     │
 │  type & value   │        │  file           │        │  send anywhere  │
 └─────────────────┘        └─────────────────┘        └─────────────────┘
```

Most AI workflows collapse at the last mile: the agent *understands* the form but can't *write to it*. FormFill closes that gap with three focused tools.

---

## Tools

| Tool | Description | When to use |
|---|---|---|
| `list_form_fields` | Returns every fillable field — name, type, and current value | First step: discover what's in the form |
| `fill_form` | Fill a PDF with provided field values and save the result | Standard forms (1–5 pages) |
| `fill_form_multipage` | Same as `fill_form`, page-by-page for large documents | Complex multi-page forms (6+ pages) |

---

## Works With

Any MCP-compatible host:

- **Claude Desktop** — add to `claude_desktop_config.json`
- **Cursor** — MCP server config
- **Cline** — same config pattern
- **Continue** — same config pattern
- Any agent that supports the Model Context Protocol

---

## Supported Form Types

| Category | Examples |
|---|---|
| Tax | W-9, W-4, 1040, Schedule C, state forms |
| HR | I-9, onboarding packets, benefits enrollment, PTO |
| Legal | NDAs, lease agreements, contracts, disclosures |
| Insurance | Claims forms, enrollment applications |
| Real Estate | Purchase agreements, rental applications, disclosures |
| Education | Admissions, financial aid, transcripts |

---

## Pricing

| Tier | Price | Monthly Fills |
|---|---|---|
| **Free** | $0 | 50 fills |
| **Pro** | $9.99 / month | Unlimited |

Get your API key at **[formfill.plenitudo.ai](https://formfill.plenitudo.ai?ref=readme)**

---

## Quick Start

### 1. Get an API key

Sign up at [formfill.plenitudo.ai](https://formfill.plenitudo.ai?ref=readme) — free tier available immediately.

### 2. Connect to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "formfill": {
      "command": "/absolute/path/to/formfill-mcp/venv/bin/python",
      "args": ["/absolute/path/to/formfill-mcp/server.py", "--stdio"]
    }
  }
}
```

Replace the paths with your actual install location. Fully quit and reopen Claude Desktop (Cmd+Q — just closing the window is not enough). You'll see the 🔨 tools icon — FormFill is connected.

**Remote HTTP endpoint** (Cursor, Cline, any HTTP MCP client):
```
https://formfill.plenitudo.ai/mcp
```

### 3. Fill your first form

```
List the fillable fields in /Users/me/Desktop/w9.pdf using API key ff_free_abc123
```

```
Fill the form at /Users/me/Desktop/w9.pdf with:
  name: Jane Smith
  TIN: 12-3456789
  address: 123 Main St, Austin TX 78701
Save to /Users/me/Desktop/w9_filled.pdf
API key: ff_free_abc123
```

---

## Example Prompts

**W-9 (tax):**
```
Fill the W-9 at ~/Desktop/fw9.pdf with my name "John Smith", SSN "123-45-6789",
address "456 Oak Ave, Boston MA 02101". Business type: Individual/sole proprietor.
Save to ~/Desktop/fw9_filled.pdf. API key: ff_free_abc123
```

**Rental application:**
```
Fill the rental application at ~/Desktop/rental_app.pdf with these values:
[paste field values]. Save to ~/Desktop/rental_filled.pdf. API key: ff_free_abc123
```

**Multi-page contract:**
```
Fill the 8-page NDA at ~/Desktop/nda.pdf. My name: Jane Smith, Company: Acme Corp,
Date: March 28 2026. Use fill_form_multipage. API key: ff_free_abc123
```

---

## Response Format

**Success:**
```json
{
  "ok": true,
  "output_path": "/Users/me/forms/w9_filled.pdf",
  "fields_filled": 12,
  "invalid_fields": []
}
```

**Error:**
```json
{"ok": false, "error": "Invalid API key"}
{"ok": false, "error": "Usage limit reached. Upgrade at https://formfill.plenitudo.ai"}
{"ok": false, "error": "File not found: /Users/me/missing.pdf"}
```

---

## Self-Hosting

```bash
git clone https://github.com/knportal/formfill-mcp.git
cd formfill-mcp

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python manage_keys.py create --tier free
python server.py
# → MCP server running on http://localhost:8000
```

For remote agent access, deploy the included Cloudflare Worker (`worker.js`).

---

## Architecture

```
server.py          — MCP server (3 tools)
auth.py            — API key validation + usage tracking (SQLite)
stripe_webhook.py  — Stripe subscription webhook handler
worker.js          — Cloudflare Worker (remote proxy)
manage_keys.py     — Key management CLI
data/keys.db       — API key store
data/usage.db      — Monthly usage counters
```

---

## Troubleshooting

**Values fill into the wrong fields**
Always call `list_form_fields` first. It returns each field's `position` (x, y on page). Use those coordinates — not guessed names — to identify fields. Higher `y` = higher on the page (PDF coordinates are bottom-up). This matters most for IRS/government PDFs that use hybrid XFA/AcroForm format.

**"No fillable fields found"**
The PDF is either flat/scanned (no AcroForm layer), XFA-only (older Adobe LiveCycle format), or password-protected. The response includes `pdf_type` to tell you which. FormFill requires interactive AcroForm fields.

**"None of the provided field names exist in this PDF"**
Field names were guessed rather than read from `list_form_fields`. The error response includes `valid_fields` — the correct names to use.

**Claude Desktop: "not a valid MCP server configuration"**
Use `command`/`args` format (not `url` or `type: streamableHttp`). Include `"--stdio"` in args. Fully quit and reopen Claude Desktop after editing the config.

**Server crashes: "Read-only file system: ./logs"**
Set `FORMFILL_LOG_FILE` to a writable path, or ensure `FORMFILL_DATA_DIR` points to a writable directory. The default `~/Library/Logs/formfill-mcp/server.log` works without config on macOS.

**API key limit reached**
Free tier: 50 fills/month. Upgrade at [formfill.plenitudo.ai](https://formfill.plenitudo.ai?ref=readme). Your key is upgraded automatically — no config change needed.

**Check server health**
- `GET https://formfill.plenitudo.ai/health` → `{"status":"ok"}`
- `GET https://formfill.plenitudo.ai/smoke-test` → fills a real PDF end-to-end, returns pass/fail per component

---

## Contributing & Security

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup and PR guidelines
- [SECURITY.md](SECURITY.md) — responsible disclosure policy

## License

[MIT](LICENSE) — Copyright © 2025 Plenitudo AI

---

<div align="center">
  <strong><a href="https://formfill.plenitudo.ai?ref=readme">formfill.plenitudo.ai</a></strong> ·
  <a href="https://github.com/knportal/formfill-mcp/issues">Issues</a> ·
  <a href="https://plenitudo.ai">Plenitudo.ai</a>
</div>
