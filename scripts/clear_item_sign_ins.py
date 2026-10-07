#!/usr/bin/env python
"""Clear the sign-ins people left in single items (`docs/plan-personal-env.md`, D10).

WHY THIS EXISTS. Before "my environment variables", signing in through a
deploy's sign-in button wrote the token into THAT item's personal values. An
item's own value still wins a name over the person's values for every item, so
those old tokens would shadow a fresh sign-in on "My environment variables" in
every item they were left in — the person signs in again and the item keeps
failing with the expired one. Nothing removes them automatically; run this when
you choose.

What it removes: from every person's values for a single item, the names some
loaded sign-in (`server.env_providers`) produces — only where that item's
policy for the name is Private first / Private only AND the person holds the
name in their values for every item too, i.e. where the item's value hides one
there (by presence, not by date: a value someone later typed into the item on
purpose goes too, so check the dry run). Nothing else — those names in Shared
items, the only copy someone who has not signed in again holds, values a person
typed under other names, their values for every item, the shared values, and
what the deploy's SSO said about them all stay. Values are never printed. So
it finds more as people sign in again on their page: run it again later.

It runs inside the API (`POST /api/admin/env/clear-item-sign-ins`), which uses
the API's own store and the sign-ins it actually loaded, so the names are this
deploy's.

The identity it runs as must be in `server.superusers`. With no header it is
whatever the deploy gives a request that carries none (`server.default_user` on
a plain deploy); behind a gateway, pass what the gateway reads with `--header`.

Usage:
    # dry run first — lists (person, item, names) and changes nothing:
    uv run python scripts/clear_item_sign_ins.py

    # then for real:
    uv run python scripts/clear_item_sign_ins.py --apply

    # non-default host / a mounted root_path:
    uv run python scripts/clear_item_sign_ins.py --base-url https://kb.example.com

    # behind a gateway that reads the identity from a header or a cookie:
    uv run python scripts/clear_item_sign_ins.py --header "X-Forwarded-User: admin"
"""

from __future__ import annotations

import argparse
import sys

import httpx

PATH = "/api/admin/env/clear-item-sign-ins"


def run(client: httpx.Client, base: str, *, apply: bool) -> int:
    resp = client.post(f"{base.rstrip('/')}{PATH}", json={"apply": apply})
    if resp.status_code == 403:
        print(
            "refused (HTTP 403): the identity this runs as is not in server.superusers"
            " — or a gateway in front of the API refused it (see --header)",
            file=sys.stderr,
        )
        return 1
    if resp.status_code != 200:
        print(f"failed: HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 1
    body = resp.json()
    rows = body["rows"]
    for row in rows:
        print(f"{row['user_id']}\t{row['item_id']}\t{', '.join(row['names'])}")
    names = sum(len(r["names"]) for r in rows)
    verb = "removed" if body["applied"] else "would remove"
    print(f"{verb} {names} name(s) in {len(rows)} (person, item) row(s)")
    if rows and not body["applied"]:
        print("dry run — nothing changed; re-run with --apply to remove them")
    return 0


def parse_headers(raw: list[str]) -> dict[str, str]:
    """`Name: value` pairs, as typed on the command line."""
    out: dict[str, str] = {}
    for item in raw:
        name, sep, value = item.partition(":")
        if not sep or not name.strip():
            raise SystemExit(f"--header expects 'Name: value', got {item!r}")
        out[name.strip()] = value.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clear the sign-ins people left in single items.")
    ap.add_argument(
        "--base-url", default="http://localhost:8000", help="app base URL (routes live under /api)"
    )
    ap.add_argument("--apply", action="store_true", help="remove them (default: dry run)")
    ap.add_argument("--timeout", type=float, default=600.0, help="request timeout (s)")
    ap.add_argument(
        "--header",
        action="append",
        default=[],
        help="'Name: value' sent with the request — the identity, behind a gateway (repeatable)",
    )
    args = ap.parse_args(argv)
    headers = parse_headers(args.header)
    with httpx.Client(timeout=args.timeout, headers=headers) as client:
        return run(client, args.base_url, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
