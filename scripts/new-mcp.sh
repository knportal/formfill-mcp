#!/usr/bin/env bash
# new-mcp.sh — Bootstrap a new MCP project from this template.
#
# Usage:
#   new-mcp my-mcp-name
#
# Prerequisites (install once):
#   brew install railway          # Railway CLI
#   npm install -g wrangler       # Cloudflare CLI
#   brew install gh               # GitHub CLI
#
# Credentials are read automatically from ~/.mcp-secrets.
# Run setup-mcp-secrets to populate that file.
#
# What this script does:
#   1. Loads credentials from ~/.mcp-secrets
#   2. Creates a new GitHub repo from this template
#   3. Clones it locally
#   4. Creates a Railway project and sets all env vars
#   5. Deploys the Cloudflare Worker and sets its origin secret
#   6. Creates a Cloudflare Pages project for the landing page
#   7. Adds all secrets to GitHub Actions via setup-mcp-secrets
#   8. Prints a checklist of remaining manual steps

set -euo pipefail

# ── Load credentials from ~/.mcp-secrets ──────────────────────────────────────
SECRETS_FILE="${HOME}/.mcp-secrets"
[[ -f "$SECRETS_FILE" ]] || { echo "~/.mcp-secrets not found. Run: setup-mcp-secrets"; exit 1; }
# shellcheck disable=SC1090
source "$SECRETS_FILE"

# ── Args ──────────────────────────────────────────────────────────────────────
NAME="${1:-}"
if [[ -z "$NAME" ]]; then
  echo "Usage: new-mcp <project-name>"
  exit 1
fi

TEMPLATE_REPO="knportal/formfill-mcp"   # Change to your org/template-repo
GITHUB_ORG="${GITHUB_ORG:-knportal}"     # Override with your org
LANDING_PROJECT="${NAME}-landing"
WORKER_NAME="${NAME}"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo -e "\033[34m[·]\033[0m $*"; }
ok()    { echo -e "\033[32m[✓]\033[0m $*"; }
warn()  { echo -e "\033[33m[!]\033[0m $*"; }
die()   { echo -e "\033[31m[✗]\033[0m $*"; exit 1; }

require() {
  command -v "$1" &>/dev/null || die "Missing required tool: $1. Install with: $2"
}

# ── Preflight ─────────────────────────────────────────────────────────────────
require gh       "brew install gh"
require railway  "brew install railway"
require wrangler "npm install -g wrangler"

[[ -n "${STRIPE_SECRET_KEY:-}" && "${STRIPE_SECRET_KEY}" != *"..."* ]] || die "STRIPE_SECRET_KEY not set in ~/.mcp-secrets"
[[ -n "${CLOUDFLARE_API_TOKEN:-}" && "${CLOUDFLARE_API_TOKEN}" != *"..."* ]] || die "CLOUDFLARE_API_TOKEN not set in ~/.mcp-secrets"
[[ -n "${CLOUDFLARE_ACCOUNT_ID:-}" && "${CLOUDFLARE_ACCOUNT_ID}" != *"..."* ]] || die "CLOUDFLARE_ACCOUNT_ID not set in ~/.mcp-secrets"

STRIPE_WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-}"
ORIGIN_URL="${ORIGIN_URL:-}"

# ── 1. Create GitHub repo from template ───────────────────────────────────────
info "Creating GitHub repo ${GITHUB_ORG}/${NAME} from template ${TEMPLATE_REPO}..."
gh repo create "${GITHUB_ORG}/${NAME}" \
  --template "${TEMPLATE_REPO}" \
  --private \
  --clone
ok "Repo created and cloned into ./${NAME}"

cd "${NAME}"

# Update wrangler.toml with the new worker name
sed -i.bak "s/name = \"formfill-mcp\"/name = \"${WORKER_NAME}\"/" wrangler.toml
# Update routes — you'll need to edit these manually for the new domain
warn "Edit wrangler.toml routes to point to your new domain before deploying."

# ── 2. Railway project ────────────────────────────────────────────────────────
info "Creating Railway project ${NAME}..."
railway init --name "${NAME}"

info "Setting Railway environment variables..."
railway variables set \
  STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY}" \
  STRIPE_WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET}" \
  FORMFILL_DATA_DIR="/data" \
  PORT="8000"
ok "Railway env vars set"

info "Deploying to Railway..."
railway up --detach
ok "Railway deploy triggered — check dashboard for live URL"

# ── 3. Cloudflare Worker ──────────────────────────────────────────────────────
if [[ -n "$ORIGIN_URL" ]]; then
  info "Deploying Cloudflare Worker ${WORKER_NAME}..."
  CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
  CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID}" \
  wrangler deploy

  info "Setting Worker secret FORMFILL_ORIGIN_URL..."
  echo "${ORIGIN_URL}" | \
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
    wrangler secret put FORMFILL_ORIGIN_URL
  ok "Worker deployed and secret set"
else
  warn "Skipping Worker deploy — set FORMFILL_ORIGIN_URL and run: wrangler deploy && wrangler secret put FORMFILL_ORIGIN_URL"
fi

# ── 4. Cloudflare Pages ───────────────────────────────────────────────────────
info "Creating Cloudflare Pages project ${LANDING_PROJECT}..."
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID}" \
wrangler pages project create "${LANDING_PROJECT}" --production-branch main || \
  warn "Pages project may already exist — skipping"

info "Deploying landing page..."
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID}" \
wrangler pages deploy landing --project-name "${LANDING_PROJECT}"
ok "Landing page deployed"

# ── 5. GitHub Actions secrets (via setup-mcp-secrets) ────────────────────────
info "Setting all GitHub Actions secrets..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${SCRIPT_DIR}/setup-secrets.sh" "${GITHUB_ORG}/${NAME}"

# ── 6. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "Project ${NAME} bootstrapped!"
echo ""
echo "Remaining manual steps:"
echo "  1. Get Railway live URL from dashboard → set FORMFILL_ORIGIN_URL"
echo "     then: wrangler secret put FORMFILL_ORIGIN_URL"
echo "  2. Update wrangler.toml routes for your new domain"
echo "  3. Create Stripe webhook → set STRIPE_WEBHOOK_SECRET on Railway"
echo "     railway variables set STRIPE_WEBHOOK_SECRET=whsec_..."
echo "  4. Update landing/index.html with your new product name, pricing, domain"
echo "  5. Push to trigger full auto-deploy:"
echo "     git add -A && git commit -m 'init' && git push"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
