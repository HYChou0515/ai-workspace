"""Schedules a PAGE declares — the other half of ``triggers.json``.

A profile's ``triggers.json`` is authored once, by whoever builds the app. This
module is what lets a domain expert say "every weekday at 09:00, build my
report" without anyone editing the repo: a WUI writes ``schedules.json`` into
its own folder and the sweep reads it.

**Declaration is data; state stays the platform's.** The page writes the file
with ``writeFile``, which REPLACES rather than appends — so pressing save five
times is one schedule, not five, and there is no idempotency key for anyone to
get wrong. The window ledger, the CAS lease, the catch-up rule and orphan
pickup are all unchanged: they key on a trigger id and do not care where the
declaration came from.

**The lease key is derived from the CONTENT, never from an id the page chose.**
That is not tidiness. A page that regenerates a random row id on every save
would look like a brand-new schedule each time, the ledger would reset, and it
would fire again for a window it had already fired for — sending the same mail
twice. LLM-written pages do exactly this. Deriving the key removes the failure
instead of documenting it.

This module is the DECLARATIVE layer only: the shape, the decode, and the
lint. Reading files, sweeping and firing land with the sweep.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from msgspec import Struct

from .triggers import Schedule

#: The periods a page may pick. `daily` / `weekly` / `monthly` are the words
#: `triggers.json` already uses — reused rather than re-spelled, so one
#: vocabulary covers both halves.
#:
#: `hourly` and `minutes` are new. The original design floored granularity at
#: half-hourly out of a fear of "many rows × fine periods", but the thing that
#: legitimately runs often — a poller asking "what is new since the watermark" —
#: is always ONE row, because the fan-out belongs inside the workflow. The
#: floor was solving a problem the count guard already solves, and two
#: mechanisms for one concern is how one of them goes stale.
EVERY = ("minutes", "hourly", "daily", "weekly", "monthly")

_DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class UserSchedule(Struct):
    """One row of a page's ``schedules.json``.

    ``payload`` is opaque: the platform hands it to the workflow untouched and
    never looks inside. Everything domain-shaped — who subscribed, which line,
    where to send it — lives there, which is what keeps the platform from
    learning what a report is.
    """

    run: str = ""
    """The workflow id to start. Capped by what the page declared in its view
    file, which is capped in turn by the profile's workflows — the same
    declaration-plus-ceiling shape `tools:` already uses."""
    every: str = "daily"
    n: int = 0
    """Only for ``every: minutes`` — the bucket width."""
    at: str = "00:00"
    dow: str = ""
    dom: int = 0
    tz: str = ""
    payload: dict[str, Any] = {}

    def as_schedule(self) -> Schedule:
        """The same row in the shape the existing window/due functions take, so
        `fire_window`, `period_target` and `is_due` are reused rather than
        reimplemented against a second definition of "what period is it"."""
        return Schedule(
            every=self.every if self.every != "minutes" else f"minutes:{self.n}",
            at=self.at,
            dow=self.dow,
            dom=self.dom,
            tz=self.tz,
        )


def parse_user_schedules(raw: str) -> list[UserSchedule]:
    """Decode a page's file. Assumes it has already passed
    :func:`validate_user_schedules` — the lint is where problems are named."""
    doc = json.loads(raw)
    rows: list[dict[str, Any]] = doc.get("schedules") or []
    out: list[UserSchedule] = []
    for row in rows:
        out.append(
            UserSchedule(
                run=str(row.get("run") or ""),
                every=str(row.get("every") or "daily"),
                n=int(row.get("n") or 0),
                at=str(row.get("at") or "00:00"),
                dow=str(row.get("dow") or ""),
                dom=int(row.get("dom") or 0),
                tz=str(row.get("tz") or ""),
                # "with" in the file because that is how it reads to an author;
                # `payload` in code because `with` is a keyword.
                payload=dict(row.get("with") or {}),
            )
        )
    return out


def validate_user_schedules(raw: str) -> list[str]:
    """Every problem with this file, as sentences. Never raises.

    Lint, not crash — the same contract ``validate_triggers`` keeps, and for a
    sharper reason here: this file is written by a page, a page is written by an
    LLM, and the sweep reads EVERY item's file. One malformed file that raised
    would stop every other item's schedules too.
    """
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return [f"schedules.json could not be read as JSON: {exc}"]
    if not isinstance(doc, dict):
        return ["schedules.json must be an object with a `schedules` list."]
    rows = doc.get("schedules")
    if rows is None:
        return ["schedules.json has no `schedules` list."]
    if not isinstance(rows, list):
        return ["`schedules` must be a list."]

    problems: list[str] = []
    for i, raw_row in enumerate(rows):
        where = f"schedules[{i}]"
        if not isinstance(raw_row, dict):
            problems.append(f"{where}: each schedule must be an object.")
            continue
        # Decoded JSON: `isinstance` proves it is a mapping but says nothing
        # about the key type, so the cast is where that claim is made once
        # rather than at every `.get` below.
        row = cast("dict[str, Any]", raw_row)
        if not row.get("run"):
            problems.append(f"{where}: needs `run` — the workflow to start.")
        every = row.get("every", "daily")
        if every not in EVERY:
            problems.append(f"{where}: `every` is {every!r}; it must be one of {', '.join(EVERY)}.")
            continue
        n = row.get("n")
        if every == "minutes":
            if not isinstance(n, int) or n < 1:
                problems.append(f"{where}: `every: minutes` needs `n` — how many minutes apart.")
        elif n:
            problems.append(f"{where}: `n` applies only to `every: minutes`.")
        if every == "weekly" and row.get("dow") not in _DOW:
            problems.append(f"{where}: a weekly schedule needs `dow` ({', '.join(_DOW)}).")
        if every == "monthly":
            dom = row.get("dom", 0)
            if dom and not (isinstance(dom, int) and 1 <= dom <= 31):
                problems.append(f"{where}: `dom` must be 1..31, got {dom!r}.")
        if every in ("daily", "weekly", "monthly"):
            at = row.get("at", "00:00")
            if not _looks_like_time(at):
                problems.append(f"{where}: `at` must look like HH:MM, got {at!r}.")
        elif row.get("at"):
            # A wall time on a repeating sub-daily period is two answers to one
            # question. Refusing it here is what lets `period_target` mean
            # exactly one thing for these — see its bucket-start branch.
            problems.append(f"{where}: `at` applies only to daily / weekly / monthly.")
    return problems


def _looks_like_time(at: object) -> bool:
    if not isinstance(at, str) or ":" not in at:
        return False
    hh, _, mm = at.partition(":")
    if not (hh.isdigit() and mm.isdigit()):
        return False
    return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def trigger_id_for(item_id: str, folder: str, row: UserSchedule) -> str:
    """This row's lease key — derived from WHAT it runs, WITH what, and WHEN.

    Not from anything the page can choose freely, and not from the row's
    position in the file. Two consequences, both wanted:

    * Re-saving an unchanged schedule keeps its key, so the ledger still knows
      it fired today and it does not fire again.
    * Two identical rows in one file collapse onto one key, so the CAS lease
      lets exactly one of them run. Nothing wants the same work twice at the
      same instant, and this makes it impossible rather than discouraged.

    The item and folder are IN the key: two pages in one item may legitimately
    want the same report at the same time, and they are different schedules.

    The prefix is not decoration. An operator reading the window ledger sees raw
    keys, and one that names where it came from is one they can act on.
    """
    when = (row.every, row.n, row.at, row.dow, row.dom, row.tz)
    fingerprint = json.dumps(
        {
            "folder": folder,
            "run": row.run,
            # `sort_keys` so `{a, b}` and `{b, a}` are one payload — otherwise a
            # page could fire twice just by re-serialising its own file.
            "with": row.payload,
            "when": when,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return f"wui:{item_id}:{digest}"
