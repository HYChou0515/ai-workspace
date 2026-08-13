"""Delete the context cards written before a card carried its evidence.

    uv run python scripts/delete_pre_evidence_cards.py --config ./config.yaml [--yes]

A card with no ``statements`` has a body somebody's model wrote from one document
and nothing to recompute it from — so a later document cannot add to it, only
overwrite it. That is the shape this whole change removes
(``docs/plan-context-card-evidence.md``), and the corpus owner decided the old
cards go rather than the pipeline carrying a compatibility path for them.

One-off and interactive on purpose: a destructive data operation should not
become code that runs on every deploy. It prints what it will delete and refuses
to do it without ``--yes``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from workspace_app.config.loader import load
from workspace_app.factories import get_spec
from workspace_app.resources import ContextCard


def main() -> None:
    p = argparse.ArgumentParser(prog="delete_pre_evidence_cards")
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    p.add_argument(
        "--collection", default=None, help="limit to one collection (default: every one)"
    )
    p.add_argument("--yes", action="store_true", help="actually delete; without it, dry run")
    args = p.parse_args()

    spec = get_spec(load(config_path=args.config))
    rm = spec.get_resource_manager(ContextCard)
    doomed, kept = [], 0
    for row in rm.list_resources():
        card = row.data
        if not isinstance(card, ContextCard):
            continue
        if args.collection and card.collection_id != args.collection:
            continue
        if card.statements:
            kept += 1
            continue
        doomed.append((row.info.resource_id, card))  # ty: ignore[unresolved-attribute]

    print(f"{len(doomed)} card(s) with no evidence; {kept} carry theirs and stay", file=sys.stderr)
    for _, card in doomed[:20]:
        print(f"  {card.title or (card.keys[:1] or ['?'])[0]}: {card.body[:60]}", file=sys.stderr)
    if len(doomed) > 20:
        print(f"  … and {len(doomed) - 20} more", file=sys.stderr)

    if not args.yes:
        print("\ndry run — pass --yes to delete", file=sys.stderr)
        return
    for card_id, _ in doomed:
        rm.delete(card_id)
    print(f"deleted {len(doomed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
