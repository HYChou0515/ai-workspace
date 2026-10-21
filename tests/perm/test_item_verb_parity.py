"""`tests/fixtures/item_verb_cases.json` is what the browser's copy of
`authorize` (`web/src/lib/itemPermission.ts` `hasItemVerb`) is held to
(`web/tests/itemVerbParity.test.ts`). Change the rule — an implication like
docs/plan-wui-user.md D9/D10 — and this reddens until the table is regenerated,
so the panel cannot draw a button the server refuses, or hide one it allows."""

import itertools
import json
from pathlib import Path

from workspace_app.perm import Actor, Permission, authorize
from workspace_app.perm.model import Verb

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "item_verb_cases.json"
# The verbs the browser asks `hasItemVerb` about, and the grant lists that can
# make them true through an implication.
_ASKED: list[Verb] = ["read_chat", "read_content", "converse", "execute", "use_wui", "edit_content"]
_GRANTABLE = ["read_chat", "read_content", "converse", "execute", "use_wui", "edit_content"]


def _cases() -> list[dict]:
    cases = []
    for n in range(len(_GRANTABLE) + 1):
        for held in itertools.combinations(_GRANTABLE, n):
            grants = {v: ["user:alice"] for v in ("read_meta", *held)}
            perm = Permission(visibility="restricted", **grants)
            answers = {
                verb: authorize(Actor.human("alice"), verb, perm, created_by="bob")
                for verb in _ASKED
            }
            cases.append({"granted": sorted(grants), "answers": answers})
    return cases


def test_the_table_is_what_authorize_answers():
    """Regenerate with `write()` below:
    `python -c "from tests.perm.test_item_verb_parity import write; write()"`."""
    assert json.loads(_FIXTURE.read_text())["cases"] == _cases()


def write() -> None:  # pragma: no cover - the regeneration helper
    _FIXTURE.write_text(
        json.dumps(
            {
                "_note": "generated from perm.authorize; see tests/perm/test_item_verb_parity.py",
                "cases": _cases(),
            },
            indent=1,
        )
        + "\n"
    )
