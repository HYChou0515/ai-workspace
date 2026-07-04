"""Cross-origin dedup helpers for the ``create_entity`` capability (#435 P3, M1-AI).

When journal-first self-dedup finds nothing (this site hasn't minted an entity), the
M1-AI mechanism asks the model whether the new entity is the SAME real thing as one an
*other* origin already filed (a human, another workflow). A match is enriched
NON-DESTRUCTIVELY (决议3): the workflow overwrites only a fenced block it owns in the
body — keyed by the capability's ``name``, so the marker itself IS the machine-vs-human
demarcation (no schema change) — and fills only *empty* frontmatter fields; it never
touches the human's title or prose. The block is OVERWRITTEN each pass, so a re-run
cannot accumulate (决议5 — idempotent by construction, not by a ledger).

``parse_match`` is the ``validate_choice`` + fail-open guard (决议8): an answer that
isn't an existing candidate id — a ``NEW`` verdict OR a hallucinated number — yields
``None`` and is treated as NEW, never a merge into a non-existent record. Because M1-AI
only ever guards a *reversible* act (a non-destructive enrich), fail-open is always safe.
"""

from __future__ import annotations

from typing import Any


def fence_markers(name: str) -> tuple[str, str]:
    """The begin/end HTML-comment markers delimiting the block a workflow ``name`` owns
    inside another origin's entity body."""
    return f"<!-- wf:{name} begin -->", f"<!-- wf:{name} end -->"


def render_contribution(name: str, args: dict[str, Any]) -> str:
    """The workflow's contribution rendered for its fenced block — the declared fields,
    stable-sorted so a re-run produces byte-identical content (no spurious churn)."""
    lines = "\n".join(f"- {k}: {v}" for k, v in sorted(args.items()))
    return f"🤖 {name}:\n{lines}"


def replace_fenced_block(body: str, name: str, content: str) -> str:
    """Overwrite the workflow-owned fenced block (marked by ``name``) with ``content``,
    appending it if absent. Overwrite (not append) is what makes cross-merge idempotent:
    a re-run replaces the same block instead of stacking another (决议5)."""
    begin, end = fence_markers(name)
    block = f"{begin}\n{content}\n{end}"
    if begin in body and end in body:
        pre = body[: body.index(begin)]
        post = body[body.index(end) + len(end) :]
        return f"{pre}{block}{post}"
    sep = "" if not body or body.endswith("\n") else "\n"
    return f"{body}{sep}{block}\n"


def match_prompt(new_args: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    """The one-shot M1-AI classification prompt: does ``new`` describe the same real thing
    as an existing candidate? The model answers a candidate number or ``NEW``."""
    listing = "\n".join(f"#{c['number']}: {c.get('title', '')}" for c in candidates)
    new_desc = ", ".join(f"{k}: {v}" for k, v in sorted(new_args.items()))
    return (
        "You are deduplicating records. Does the NEW item describe the SAME real thing as "
        "one of the EXISTING items?\n\n"
        f"NEW: {new_desc}\n\nEXISTING:\n{listing}\n\n"
        "Answer with ONLY the matching item's number (e.g. 7), or NEW if none match."
    )


def parse_match(answer: str, candidate_numbers: list[int]) -> int | None:
    """validate_choice + fail-open (决议8): return the matched number only when the
    model's answer is an EXISTING candidate; a ``NEW`` answer OR a hallucinated
    (non-candidate) number both yield ``None`` — never a merge into a record that isn't
    there. The first whitespace token is taken, tolerating a leading ``#`` / trailing
    punctuation."""
    first = answer.strip().split()[0] if answer.strip() else ""
    token = first.lstrip("#").rstrip(".,")
    if token.isdigit() and int(token) in candidate_numbers:
        return int(token)
    return None
