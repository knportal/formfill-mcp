"""
FormFill MCP — Stripe webhook handler.

Runs as a standalone Flask app on port 8090.
Handles:
  customer.subscription.created  → upgrade key to pro
  customer.subscription.deleted  → downgrade key to free

The Stripe customer metadata must include the key 'formfill_api_key'
containing the user's API key so we know which row to update.

Start with:
    python stripe_webhook.py

Or via a process manager (systemd / pm2 / supervisor).
"""

import logging
import os
import sys

import stripe
from flask import Flask, abort, jsonify, request

from auth import set_key_tier
from config import LOG_FILE, LOG_LEVEL, STRIPE_WEBHOOK_SECRET

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured — rejecting webhook.")
        abort(500, "Webhook secret not configured.")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Stripe webhook: invalid payload")
        abort(400, "Invalid payload.")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook: signature verification failed")
        abort(400, "Invalid signature.")

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("Stripe event received: %s  id=%s", event_type, event["id"])

    # -----------------------------------------------------------------------
    # Subscription created → upgrade to pro
    # -----------------------------------------------------------------------
    if event_type == "customer.subscription.created":
        customer_id = data.get("customer")
        api_key = _api_key_from_customer(customer_id, stripe_data=data)
        if api_key:
            changed = set_key_tier(api_key, "pro", stripe_customer=customer_id)
            if changed:
                logger.info("Upgraded key %s… to pro (customer=%s)", api_key[:16], customer_id)
            else:
                logger.warning("Key not found for upgrade: %s…", api_key[:16])
        else:
            logger.warning(
                "subscription.created: no formfill_api_key in customer metadata (customer=%s)",
                customer_id,
            )

    # -----------------------------------------------------------------------
    # Subscription deleted → downgrade to free
    # -----------------------------------------------------------------------
    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        api_key = _api_key_from_customer(customer_id, stripe_data=data)
        if api_key:
            changed = set_key_tier(api_key, "free", stripe_customer=customer_id)
            if changed:
                logger.info("Downgraded key %s… to free (customer=%s)", api_key[:16], customer_id)
            else:
                logger.warning("Key not found for downgrade: %s…", api_key[:16])
        else:
            logger.warning(
                "subscription.deleted: no formfill_api_key in customer metadata (customer=%s)",
                customer_id,
            )

    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)

    return jsonify({"ok": True})


def _api_key_from_customer(customer_id: str, stripe_data: dict) -> str | None:
    """
    Retrieve the FormFill API key stored in the Stripe customer's metadata.

    Stripe subscription objects don't directly embed customer metadata, so we
    fetch the customer object when needed.
    """
    # Some events embed metadata on the subscription itself (custom flow)
    meta = stripe_data.get("metadata", {})
    if meta.get("formfill_api_key"):
        return meta["formfill_api_key"]

    # Fall back to fetching the customer object
    try:
        customer = stripe.Customer.retrieve(customer_id)
        return customer.get("metadata", {}).get("formfill_api_key")
    except Exception as exc:
        logger.error("Failed to retrieve Stripe customer %s: %s", customer_id, exc)
        return None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "formfill-stripe-webhook"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("STRIPE_WEBHOOK_PORT", "8090"))
    logger.info("Stripe webhook handler starting on port %d", port)
    app.run(host="0.0.0.0", port=port)
