"""Turning runs into something a person can act on.

Kept apart from the CLI so the shape of a report is unit-tested rather than
eyeballed in a terminal.
"""

from __future__ import annotations

import msgspec

from .runner import Transcript
from .scenario import Scenario, Verdict, check


class Row(msgspec.Struct, frozen=True):
    scenario: str
    verdict: Verdict
    calls: list[str]
    ended: str
    note: str = ""


class Report(msgspec.Struct, frozen=True):
    skill: str
    model: str
    rows: list[Row]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r.verdict.passed)


def row_for(scenario: Scenario, transcript: Transcript) -> Row:
    return Row(
        scenario=scenario.name,
        verdict=check(scenario, transcript.calls, transcript.answer),
        calls=transcript.calls,
        ended=transcript.ended,
        note=scenario.note,
    )


def render(report: Report, *, control: dict[str, Row] | None = None) -> str:
    """A short text table. The control column is the point of the whole exercise:
    a scenario the skill passes AND the control also passes proves nothing about
    the guidance."""
    width = max([len(r.scenario) for r in report.rows] + [8])
    head = f"{'scenario'.ljust(width)}  skill"
    if control:
        head += "   control"
    lines = [f"{report.skill}  ×  {report.model}", "", head, "-" * len(head)]
    for r in report.rows:
        mark = "pass" if r.verdict.passed else "FAIL"
        line = f"{r.scenario.ljust(width)}  {mark:5}"
        if control:
            c = control.get(r.scenario)
            line += f"  {('pass' if c.verdict.passed else 'FAIL') if c else '  -  '}"
        lines.append(line)
        for f in r.verdict.failures:
            lines.append(f"{' ' * width}    {f.rule}: {f.detail}")
    lines.append("")
    lines.append(f"{report.passed}/{len(report.rows)} scenarios pass with the skill applied")
    if control:
        both = [
            r.scenario
            for r in report.rows
            if r.verdict.passed and (c := control.get(r.scenario)) and c.verdict.passed
        ]
        if both:
            lines.append(
                f"no evidence from {', '.join(both)} — the control passes them too, "
                "so they do not measure the guidance"
            )
    return "\n".join(lines)
