"""
FormFill MCP — x402 micropayment verification (USDC on Base).

Implements the x402 (HTTP 402 Payment Required) protocol:
  1. Client calls tool without payment → gets payment instructions
  2. Client sends USDC on Base to the wallet address
  3. Client calls tool with tx hash as payment_proof → server verifies on-chain

Verification is done via JSON-RPC (eth_getTransactionReceipt) — no web3 dependency.
Replay attacks are prevented via a SQLite table of used tx hashes.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import requests

from config import DATA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WALLET_ADDRESS = os.getenv(
    "X402_WALLET_ADDRESS",
    "0x9053FeDC90c1BCB4a8Cf708DdB426aB02430d6ad",
).lower()

PRICE_USDC = float(os.getenv("X402_PRICE_USDC", "0.001"))

# USDC contract on Base mainnet
USDC_CONTRACT_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower()

# Base mainnet JSON-RPC endpoint
BASE_RPC = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

# ERC-20 Transfer(address,address,uint256) event topic
TRANSFER_EVENT_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# USDC has 6 decimals
USDC_DECIMALS = 6

# ---------------------------------------------------------------------------
# Replay-prevention database
# ---------------------------------------------------------------------------

_PROOF_DB = os.path.join(DATA_DIR, "x402_proofs.db")

_PROOF_SCHEMA = """
CREATE TABLE IF NOT EXISTS used_proofs (
    tx_hash   TEXT PRIMARY KEY,
    used_at   TEXT NOT NULL,
    tool      TEXT NOT NULL
);
"""


@contextmanager
def _proof_conn():
    os.makedirs(os.path.dirname(_PROOF_DB), exist_ok=True)
    conn = sqlite3.connect(_PROOF_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_PROOF_SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


def is_proof_used(tx_hash: str) -> bool:
    """Check whether a tx hash has already been redeemed."""
    tx_hash = tx_hash.lower().strip()
    with _proof_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM used_proofs WHERE tx_hash = ?", (tx_hash,)
        ).fetchone()
    return row is not None


def mark_proof_used(tx_hash: str, tool_name: str) -> None:
    """Record a tx hash as redeemed so it cannot be replayed."""
    tx_hash = tx_hash.lower().strip()
    now = datetime.now(timezone.utc).isoformat()
    with _proof_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO used_proofs (tx_hash, used_at, tool) VALUES (?, ?, ?)",
            (tx_hash, now, tool_name),
        )
        conn.commit()
    logger.info("x402: marked proof used tx=%s tool=%s", tx_hash[:16], tool_name)


# ---------------------------------------------------------------------------
# Payment instructions (returned when no auth is provided)
# ---------------------------------------------------------------------------

def payment_required_response(tool_name: str) -> dict:
    """
    Return a dict that tells the client how to pay via x402.

    The client should send USDC on Base to WALLET_ADDRESS, then re-call the
    tool with the tx hash in the `payment_proof` parameter.
    """
    return {
        "ok": False,
        "error": "Payment required",
        "x402": {
            "protocol": "x402",
            "network": "base",
            "token": "USDC",
            "token_contract": USDC_CONTRACT_BASE,
            "recipient": WALLET_ADDRESS,
            "amount_usdc": PRICE_USDC,
            "amount_raw": int(PRICE_USDC * 10**USDC_DECIMALS),
            "decimals": USDC_DECIMALS,
            "instructions": (
                f"Send {PRICE_USDC} USDC on Base to {WALLET_ADDRESS}, "
                f"then re-call {tool_name} with payment_proof=<tx_hash>."
            ),
        },
    }


# ---------------------------------------------------------------------------
# On-chain verification via JSON-RPC
# ---------------------------------------------------------------------------

def _rpc_call(method: str, params: list) -> dict:
    """Make a JSON-RPC call to the Base RPC endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    resp = requests.post(BASE_RPC, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data.get("result")


def verify_payment(
    tx_hash: str,
    expected_amount_usdc: float,
    recipient_address: str,
) -> tuple[bool, str | None]:
    """
    Verify that a Base transaction is a valid USDC payment.

    Checks:
      - Transaction exists and succeeded (status 0x1)
      - Contains a USDC Transfer event log
      - Recipient matches expected address
      - Amount >= expected (with 1% slippage tolerance)

    Returns:
        (True, None) on success, or (False, error_message) on failure.
    """
    tx_hash = tx_hash.lower().strip()
    recipient_address = recipient_address.lower()

    # Validate tx hash format
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return False, "Invalid transaction hash format (expected 0x + 64 hex chars)."

    try:
        receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
    except Exception as exc:
        logger.error("x402: RPC call failed for tx %s: %s", tx_hash[:16], exc)
        return False, f"Could not fetch transaction receipt: {exc}"

    if receipt is None:
        return False, "Transaction not found. It may still be pending — wait for confirmation and retry."

    # Check tx succeeded
    status = receipt.get("status", "0x0")
    if status != "0x1":
        return False, "Transaction failed on-chain (status != 0x1)."

    # Parse logs for a USDC Transfer event to our address
    logs = receipt.get("logs", [])
    expected_raw = int(expected_amount_usdc * 10**USDC_DECIMALS * 0.99)  # 1% slippage

    for log in logs:
        log_address = log.get("address", "").lower()
        topics = log.get("topics", [])
        data = log.get("data", "0x0")

        # Must be from the USDC contract
        if log_address != USDC_CONTRACT_BASE:
            continue

        # Must be a Transfer event with 3 topics (signature, from, to)
        if len(topics) < 3:
            continue
        if topics[0].lower() != TRANSFER_EVENT_TOPIC:
            continue

        # topics[2] is the 'to' address (zero-padded to 32 bytes)
        log_to = "0x" + topics[2][-40:].lower()
        if log_to != recipient_address:
            continue

        # data holds the uint256 amount
        try:
            amount = int(data, 16)
        except (ValueError, TypeError):
            continue

        if amount >= expected_raw:
            logger.info(
                "x402: verified payment tx=%s amount=%d (expected>=%d)",
                tx_hash[:16],
                amount,
                expected_raw,
            )
            return True, None
        else:
            return False, (
                f"Transfer amount too low: got {amount / 10**USDC_DECIMALS:.6f} USDC, "
                f"expected >= {expected_amount_usdc} USDC."
            )

    return False, "No matching USDC Transfer event found in transaction logs."
