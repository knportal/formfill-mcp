# Agent Instructions — Plenitudo AI MCP Projects

## Creating a New MCP

When the user asks to create a new MCP app, run:

```bash
new-mcp <project-name>
```

This single command does everything:
- Creates a GitHub repo from the formfill-mcp template
- Sets up Railway (backend), Cloudflare Worker (proxy), Cloudflare Pages (landing)
- Sets all GitHub Actions secrets from ~/.mcp-secrets
- Every subsequent `git push` auto-deploys all three layers

## Deploying Changes

Just push. GitHub Actions handles everything:

```bash
git add -A && git commit -m "description" && git push
```

On push to `main`:
- **Railway** redeploys the Python backend automatically (GitHub integration)
- **Cloudflare Worker** redeploys via GitHub Actions (`.github/workflows/deploy.yml`)
- **Cloudflare Pages** redeploys the landing page automatically (GitHub integration)

## Credentials

All credentials are stored in `~/.mcp-secrets`. Never ask the user for API keys —
they are already available. If a new token is needed, add it to `~/.mcp-secrets`
and run `setup-mcp-secrets <repo>` to push it to GitHub.

## Architecture

```
server.py           — MCP server + API endpoints (FastMCP, Python)
auth.py             — API key validation + usage tracking (SQLite)
stripe_webhook.py   — Stripe subscription webhook handler
worker.js           — Cloudflare Worker (thin proxy to Railway backend)
landing/index.html  — Landing page (static, served via Cloudflare Pages)
manage_keys.py      — Key management CLI
scripts/new-mcp.sh  — Bootstrap script for new MCP projects
scripts/setup-secrets.sh — Push credentials to GitHub Actions secrets
```

## Key Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/signup` | Issue free API key |
| `POST /api/checkout` | Create Stripe Pro checkout session |
| `GET /api/billing?api_key=...` | Stripe billing portal redirect |
| `GET /health` | Health check |
| `GET /smoke-test` | End-to-end test |
| `POST /mcp` | MCP protocol endpoint |

## Stack

- **Backend**: Python, FastMCP, uvicorn → deployed on Railway
- **Proxy**: Cloudflare Worker (worker.js) → routes custom domain to Railway
- **Landing**: Static HTML → Cloudflare Pages
- **Payments**: Stripe (checkout + billing portal + webhooks)
- **Auth/DB**: SQLite on Railway persistent volume at `/data`
