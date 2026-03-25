#!/usr/bin/env python3
"""
FormFill MCP — API key management CLI.

Usage:
    python manage_keys.py create --tier free
    python manage_keys.py create --tier pro
    python manage_keys.py create --tier pro --customer cus_abc123
    python manage_keys.py list
    python manage_keys.py usage <api_key>
    python manage_keys.py deactivate <api_key>
"""

import argparse
import json
import os
import sys

# Ensure the project root is on the path so sibling modules resolve correctly
sys.path.insert(0, os.path.dirname(__file__))

from auth import create_key, deactivate_key, get_usage, list_keys


def cmd_create(args):
    key = create_key(tier=args.tier, stripe_customer=args.customer or None)
    print(f"\nAPI key created successfully.\n")
    print(f"  Key  : {key}")
    print(f"  Tier : {args.tier}")
    if args.customer:
        print(f"  Stripe customer: {args.customer}")
    print()
    print("Store this key securely — it will not be shown again.")
    print()


def cmd_list(args):
    keys = list_keys()
    if not keys:
        print("No API keys found.")
        return

    header = f"{'KEY':45}  {'TIER':6}  {'ACTIVE':6}  {'CREATED'}"
    print(header)
    print("-" * len(header))
    for k in keys:
        active = "yes" if k["active"] else "no"
        created = k["created_at"][:19]  # trim microseconds
        print(f"{k['key']:45}  {k['tier']:6}  {active:6}  {created}")
    print(f"\nTotal: {len(keys)} key(s)")


def cmd_usage(args):
    stats = get_usage(args.api_key)
    print(json.dumps(stats, indent=2))


def cmd_deactivate(args):
    changed = deactivate_key(args.api_key)
    if changed:
        print(f"Key deactivated: {args.api_key}")
    else:
        print(f"Key not found: {args.api_key}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="FormFill MCP — API key management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new API key")
    p_create.add_argument(
        "--tier",
        choices=["free", "pro"],
        required=True,
        help="Tier for the new key",
    )
    p_create.add_argument(
        "--customer",
        default=None,
        metavar="STRIPE_CUSTOMER_ID",
        help="Associate a Stripe customer ID with this key",
    )

    # list
    sub.add_parser("list", help="List all API keys")

    # usage
    p_usage = sub.add_parser("usage", help="Show usage stats for an API key")
    p_usage.add_argument("api_key", help="The API key to query")

    # deactivate
    p_deactivate = sub.add_parser("deactivate", help="Deactivate an API key")
    p_deactivate.add_argument("api_key", help="The API key to deactivate")

    args = parser.parse_args()

    dispatch = {
        "create": cmd_create,
        "list": cmd_list,
        "usage": cmd_usage,
        "deactivate": cmd_deactivate,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
