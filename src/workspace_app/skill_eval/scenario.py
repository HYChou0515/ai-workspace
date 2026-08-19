"""A scenario: one question put to a skill, and what a good answer must look like.

Second parties write these, not us. Guidance is model-dependent — a prompt tuned
for one model is not tuned for the next — so whoever deploys a skill needs to
state what "working" means for THEIR model and THEIR data, then edit the skill
and re-run. That is what this file's format is for.

Expectations are checked deterministically against the transcript. No LLM judge:
these questions ("did it call ask_user", "did the answer name the dtype") have
objective answers, and a judge would only add a second thing needing calibration.
"""

from __future__ import annotations

import json
from pathlib import Path

import msgspec

#: One expectation entry is a string, or a list of alternatives any one of which
#: satisfies it. Alternatives keep an expectation from being brittle about
#: wording without loosening what it actually demands.
Phrase = str | list[str]


class Expect(msgspec.Struct, frozen=True):
    """What the transcript must show. Every field defaults to "don't care", so a
    scenario states only what it means to state."""

    must_call: list[str] = msgspec.field(default_factory=list)
    must_not_call: list[str] = msgspec.field(default_factory=list)
    must_mention: list[Phrase] = msgspec.field(default_factory=list)
    must_not_mention: list[Phrase] = msgspec.field(default_factory=list)


class Scenario(msgspec.Struct, frozen=True):
    """A data file, the question asked about it, and the expectations."""

    name: str
    prompt: str
    data: list[str] = msgspec.field(default_factory=list)
    expect: Expect = msgspec.field(default_factory=Expect)
    #: Free text for a human reading the report — why this scenario exists.
    note: str = ""


class Failure(msgspec.Struct, frozen=True):
    rule: str
    detail: str


class Verdict(msgspec.Struct, frozen=True):
    scenario: str
    failures: list[Failure]

    @property
    def passed(self) -> bool:
        return not self.failures


def _alternatives(phrase: Phrase) -> list[str]:
    return [phrase] if isinstance(phrase, str) else list(phrase)


def _present(phrase: Phrase, haystack: str) -> str | None:
    """The alternative that matched, or None. Case-insensitive: an expectation
    about wording should not turn on capitalisation."""
    low = haystack.lower()
    for alt in _alternatives(phrase):
        if alt.lower() in low:
            return alt
    return None


def check(scenario: Scenario, calls: list[str], answer: str) -> Verdict:
    """Score one run. ``calls`` is the tool names in order; ``answer`` is the
    model's final text."""
    e, failures = scenario.expect, []
    seen = set(calls)
    for name in e.must_call:
        if name not in seen:
            got = calls or "(none)"
            failures.append(Failure("must_call", f"never called {name!r}; called {got}"))
    for name in e.must_not_call:
        if name in seen:
            failures.append(Failure("must_not_call", f"called {name!r}"))
    for phrase in e.must_mention:
        if _present(phrase, answer) is None:
            alts = _alternatives(phrase)
            failures.append(Failure("must_mention", f"answer names none of {alts}"))
    for phrase in e.must_not_mention:
        hit = _present(phrase, answer)
        if hit is not None:
            failures.append(Failure("must_not_mention", f"answer names {hit!r}"))
    return Verdict(scenario=scenario.name, failures=failures)


def load_scenarios(folder: Path) -> list[Scenario]:
    """Every ``*.json`` in ``folder``, by filename. A scenario's ``data`` files
    are resolved relative to the same folder."""
    out = []
    for path in sorted(folder.glob("*.json")):
        out.append(msgspec.convert(json.loads(path.read_text()), Scenario))
    return out
