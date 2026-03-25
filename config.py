"""
FormFill MCP — Configuration
All values are read from environment variables with safe defaults.
"""

import os

# Stripe
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID = os.getenv("STRIPE_PRO_PRICE_ID", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Database base directory  (trailing slash is intentional; DB filenames are appended)
DB_PATH = os.path.expanduser(
    os.getenv("FORMFILL_DB_PATH", "~/Projects/formfill-mcp/data/")
)

# Derived DB paths
KEYS_DB = os.path.join(DB_PATH, "keys.db")
USAGE_DB = os.path.join(DB_PATH, "usage.db")

# Tier limits
FREE_MONTHLY_LIMIT = int(os.getenv("FREE_MONTHLY_LIMIT", "50"))

# Log file
LOG_FILE = os.path.expanduser(
    os.getenv("FORMFILL_LOG_FILE", "~/Projects/formfill-mcp/logs/server.log")
)

# Upgrade URL surfaced in error messages
UPGRADE_URL = os.getenv("UPGRADE_URL", "https://formfill.plenitudo.ai")
