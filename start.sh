#!/bin/bash
cd ~/Projects/formfill-mcp
source venv/bin/activate

export STRIPE_PRO_PRICE_ID="price_1TEpz0D8su7Z5VwBjKlDzqMC"
export STRIPE_SECRET_KEY="$(cat .secrets/stripe-live.key)"
export STRIPE_WEBHOOK_SECRET="$(cat .secrets/stripe-webhook.key)"
export UPGRADE_URL="https://formfill.plenitudo.ai"

python server.py --port 8000
