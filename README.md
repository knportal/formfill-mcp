# FormFill MCP

Fill PDF forms from any AI agent that supports the Model Context Protocol.
Built for [Plenitudo.ai](https://plenitudo.ai).

---

## What it is

FormFill MCP exposes three tools that let Claude (and other MCP-compatible agents) fill interactive PDF forms programmatically:

| Tool | What it does |
|---|---|
| `list_form_fields` | Inspect a PDF and return every fillable field name and type |
| `fill_form` | Fill a PDF form and save the result |
| `fill_form_multipage` | Same as `fill_form`, optimised for multi-page forms |

All tools use **file paths** rather than base64 encoding, so even large PDFs are handled cleanly.

---

## Pricing

| Tier | Price | Fills |
|---|---|---|
| Free | $0 | 50 fills / month |
| Pro | $9.99 / month | Unlimited |

Get an API key at **[formfill.plenitudo.ai](https://formfill.plenitudo.ai)**.

---

## Setup

### 1. Install dependencies

```bash
cd ~/Projects/formfill-mcp
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set STRIPE_WEBHOOK_SECRET if running the webhook handler
```

### 3. Create an API key (self-hosted / dev)

```bash
python manage_keys.py create --tier free
# → ff_free_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 4. Add to Claude Desktop

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

---

## Example prompts for Claude Desktop

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

**Fill a multi-page application:**
```
Fill the 3-page rental application at /Users/me/forms/rental_app.pdf
with these values: [paste field values]. Save to /Users/me/Desktop/rental_filled.pdf.
api_key: ff_free_abc123
```

---

## Running the Stripe webhook handler

Required only if you're processing Pro subscriptions:

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

The Stripe customer must have `formfill_api_key` in their metadata.

---

## Cloudflare Worker (remote access)

For remote agents that can't reach your local machine, deploy the included Cloudflare Worker:

```bash
npm install -g wrangler
wrangler login
wrangler secret put FORMFILL_ORIGIN_URL   # your server's public URL
wrangler deploy
```

See `worker.js` for full instructions.

---

## Key management CLI

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

## Support

[formfill.plenitudo.ai](https://formfill.plenitudo.ai)
