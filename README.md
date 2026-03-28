# FormFill MCP

**An MCP server for Claude, Cursor, and any AI agent — fill any PDF form in one tool call.**
Tax forms, HR paperwork, legal documents, lease agreements — if it has fillable fields, FormFill can fill it.

[![smithery badge](https://smithery.ai/badge/formfill-mcp)](https://smithery.ai/server/formfill-mcp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](smithery.yaml)

Built by [Plenitudo AI](https://plenitudo.ai).

---

## Why FormFill?

Most AI workflows that touch PDFs fall apart at the last mile: the agent understands the form but can't actually write to it. FormFill closes that gap.

- **One tool call** — inspect and fill any PDF form without writing a single line of code
- **No base64 nightmares** — tools use file paths, so large PDFs work cleanly
- **Any agent, any platform** — works with Claude Desktop, Cursor, Cline, Continue, and any MCP-compatible host

---

## Tools

| Tool | What it does |
|---|---|
| `list_form_fields` | Inspect a PDF and return every fillable field name, type, and current value |
| `fill_form` | Fill a PDF form with provided field values and save the result |
| `fill_form_multipage` | Optimised for PDFs with more than 5 pages or fields spanning multiple pages |

### Typical workflow

```
1. list_form_fields  →  discover field names in the PDF
2. fill_form         →  write values, save filled copy
```

---

## Supported Form Types

FormFill works with any interactive (AcroForm) PDF. Common use cases:

- **Tax:** W-9, W-4, 1040, Schedule C, state tax forms
- **HR:** I-9, onboarding packets, benefits enrollment, PTO requests
- **Legal:** NDAs, lease agreements, contracts with signature fields
- **Insurance:** claims forms, enrollment applications
- **Real estate:** purchase agreements, disclosure forms, rental applications
- **Education:** admissions forms, transcripts, financial aid paperwork

---

## Pricing

| Tier | Price | Fills |
|---|---|---|
| Free | $0 | 50 fills / month |
| Pro | $9.99 / month | Unlimited |

Get an API key at **[formfill.plenitudo.ai](https://formfill.plenitudo.ai)**.

---

## Quick Start

### Step 1 — Get an API key

Visit [formfill.plenitudo.ai](https://formfill.plenitudo.ai) and sign up for a free key.

### Step 2 — Add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "formfill": {
      "command": "/Users/YOUR_USERNAME/Projects/formfill-mcp/venv/bin/python",
      "args": ["/Users/YOUR_USERNAME/Projects/formfill-mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop.

### Step 3 — Fill a form

```
List the fillable fields in /Users/me/forms/w9.pdf using API key ff_free_abc123
```

```
Fill the form at /Users/me/forms/w9.pdf with name "Jane Smith", TIN "12-3456789",
address "123 Main St, Austin TX 78701". Save to /Users/me/forms/w9_filled.pdf.
API key: ff_free_abc123
```

---

## Self-Hosting Setup

### 1. Install dependencies

```bash
git clone https://github.com/knportal/formfill-mcp.git
cd formfill-mcp

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set STRIPE_WEBHOOK_SECRET if running the webhook handler
```

### 3. Create an API key (dev/self-hosted)

```bash
python manage_keys.py create --tier free
# → ff_free_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 4. Run the server

```bash
python server.py
# MCP server starts on http://localhost:8000
```

---

## Example Prompts

**List fields in a form:**
```
List the fillable fields in /Users/me/forms/w9.pdf using API key ff_free_abc123
```

**Fill a W-9:**
```
Fill the form at /Users/me/forms/w9.pdf with my name "Jane Smith", TIN "12-3456789",
and address "123 Main St, Austin TX 78701". Save to /Users/me/forms/w9_filled.pdf.
Use API key ff_free_abc123.
```

**Fill a multi-page rental application:**
```
Fill the 3-page rental application at /Users/me/forms/rental_app.pdf
with these values: [paste field values]. Save to /Users/me/Desktop/rental_filled.pdf.
api_key: ff_free_abc123
```

---

## Error Handling

All tools return JSON. On error, the response includes `"ok": false` and an `"error"` field:

```json
{"ok": false, "error": "Invalid API key"}
{"ok": false, "error": "Usage limit reached. Upgrade at https://formfill.plenitudo.ai"}
{"ok": false, "error": "File not found: /Users/me/missing.pdf"}
{"ok": false, "error": "Field 'unknown_field' not found in form"}
```

On success, `fill_form` returns:

```json
{
  "ok": true,
  "output_path": "/Users/me/forms/w9_filled.pdf",
  "fields_filled": 12,
  "invalid_fields": []
}
```

---

## Stripe Webhook Handler

Required only if processing Pro subscriptions:

```bash
source venv/bin/activate
python stripe_webhook.py
# Listens on port 8090 by default
```

Point your Stripe webhook to:
```
https://your-domain.com/webhook/stripe
```

Events handled:
- `customer.subscription.created` → upgrades key to Pro
- `customer.subscription.deleted` → downgrades key to Free

---

## Cloudflare Worker (Remote Access)

For remote agents that can't reach your local machine, deploy the included Cloudflare Worker:

```bash
npm install -g wrangler
wrangler login
wrangler secret put FORMFILL_ORIGIN_URL   # your server's public URL
wrangler deploy
```

See `worker.js` for full instructions.

---

## Key Management CLI

```bash
# Create a free key
python manage_keys.py create --tier free

# Create a pro key (after Stripe payment)
python manage_keys.py create --tier pro --customer cus_abc123

# List all keys
python manage_keys.py list

# Check usage for a key
python manage_keys.py usage ff_free_abc123

# Deactivate a key
python manage_keys.py deactivate ff_free_abc123
```

---

## Architecture

```
server.py          — MCP server (the three tools)
auth.py            — API key validation + usage tracking (SQLite)
usage.py           — Usage helpers
config.py          — Environment variable configuration
stripe_webhook.py  — Flask webhook handler for Stripe events
worker.js          — Cloudflare Worker (remote proxy)
manage_keys.py     — CLI for key management
data/keys.db       — API key store
data/usage.db      — Monthly usage counters
logs/server.log    — Structured log file
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and PR guidelines.

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) for our responsible disclosure policy.

## License

[MIT](LICENSE) — Copyright © 2024 Plenitudo AI

---

**[formfill.plenitudo.ai](https://formfill.plenitudo.ai)** · [Issues](https://github.com/knportal/formfill-mcp/issues) · [Plenitudo.ai](https://plenitudo.ai)
