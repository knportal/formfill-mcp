#!/usr/bin/env bash
# setup-secrets.sh — Push all credentials to a GitHub repo + Railway project.
#
# Run once per new MCP project, or re-run to rotate tokens.
#
# Usage:
#   ./scripts/setup-secrets.sh <github-repo>          # e.g. knportal/my-new-mcp
#   ./scripts/setup-secrets.sh <github-repo> <railway-project-id>
#
# Reads credentials from ~/.mcp-secrets (create from template below if missing).

set -euo pipefail

REPO="${1:-}"
RAILWAY_PROJECT="${2:-}"

if [[ -z "$REPO" ]]; then
  echo "Usage: $0 <github-repo> [railway-project-id]"
  echo "  e.g. $0 knportal/my-new-mcp"
  exit 1
fi

# ── Load secrets ──────────────────────────────────────────────────────────────
SECRETS_FILE="${HOME}/.mcp-secrets"
if [[ ! -f "$SECRETS_FILE" ]]; then
  cat > "$SECRETS_FILE" << 'TEMPLATE'
# ~/.mcp-secrets — fill these in once, never touch again.
# Keep this file private — never commit it.

# Cloudflare
CLOUDFLARE_API_TOKEN=cfut_...
CLOUDFLARE_ACCOUNT_ID=df6f8981625ee646b67370e97b3b85d6

# Railway
RAILWAY_TOKEN=...

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PRICE_ID=price_...

# Vercel (optional, if using Vercel instead of Cloudflare Pages)
VERCEL_TOKEN=...
VERCEL_ORG_ID=...
TEMPLATE
  chmod 600 "$SECRETS_FILE"
  echo "Created ~/.mcp-secrets — fill it in and re-run."
  exit 1
fi

# shellcheck disable=SC1090
source "$SECRETS_FILE"

info()  { echo -e "\033[34m[·]\033[0m $*"; }
ok()    { echo -e "\033[32m[✓]\033[0m $*"; }
warn()  { echo -e "\033[33m[!]\033[0m $*"; }

# ── GitHub repo secrets ───────────────────────────────────────────────────────
info "Setting GitHub secrets on ${REPO}..."

set_secret() {
  local name="$1"
  local value="$2"
  if [[ -n "$value" && "$value" != *"..."* ]]; then
    gh secret set "$name" --body "$value" --repo "$REPO"
    ok "  $name"
  else
    warn "  $name — skipped (not set in ~/.mcp-secrets)"
  fi
}

set_secret CLOUDFLARE_API_TOKEN    "${CLOUDFLARE_API_TOKEN:-}"
set_secret CLOUDFLARE_ACCOUNT_ID   "${CLOUDFLARE_ACCOUNT_ID:-}"
set_secret RAILWAY_TOKEN           "${RAILWAY_TOKEN:-}"
set_secret STRIPE_SECRET_KEY       "${STRIPE_SECRET_KEY:-}"
set_secret STRIPE_PRICE_ID         "${STRIPE_PRICE_ID:-}"
[[ -n "${VERCEL_TOKEN:-}" ]]       && set_secret VERCEL_TOKEN  "${VERCEL_TOKEN}"
[[ -n "${VERCEL_ORG_ID:-}" ]]      && set_secret VERCEL_ORG_ID "${VERCEL_ORG_ID}"

# ── Railway env vars ──────────────────────────────────────────────────────────
if [[ -n "$RAILWAY_PROJECT" ]]; then
  info "Setting Railway env vars for project ${RAILWAY_PROJECT}..."
  command -v railway &>/dev/null || { warn "railway CLI not installed — skipping"; }
  railway variables set \
    STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY:-}" \
    FORMFILL_DATA_DIR="/data" \
    PORT="8000" \
    --project "$RAILWAY_PROJECT" 2>/dev/null && ok "Railway vars set" || warn "Railway vars — check manually"
else
  warn "No Railway project ID provided — skipping Railway vars"
  warn "Run: railway variables set STRIPE_SECRET_KEY=... (in your project dir)"
fi

echo ""
ok "Done. Secrets are set on ${REPO}."
echo "   Next push will deploy automatically via GitHub Actions."
