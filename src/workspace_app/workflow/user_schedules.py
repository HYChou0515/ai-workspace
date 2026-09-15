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

import functools
import hashlib
import json
import logging
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from msgspec import Struct

from .triggers import Schedule, _valid_tz, next_run
from .workspace_store import SCHEDULES_FILE, WORKSPACE_WORKFLOW_DIR

logger = logging.getLogger(__name__)

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


@functools.cache
def _zones_by_lowercase() -> dict[str, str]:
    """Every IANA zone this machine knows, keyed by its lowercase spelling.

    Built once. `available_timezones()` walks the tzdata tree, which is far too
    much to do per row of every page's file on every tick.
    """
    return {name.lower(): name for name in available_timezones()}


def normalise_tz(tz: str) -> str:
    """The zone as `ZoneInfo` spells it, or the input unchanged when it is not a
    zone at all.

    IANA names are case-sensitive by convention only, and `utc` / `asia/taipei`
    is how people type them. Refusing those turned "the report arrives an hour
    out" into "the report stops" — the worse of the two, because a wrong time is
    noticed and an absent report is noticed weeks later. This module's own design
    note says exactly that about silent stops.

    Deliberately NOT clever beyond case. `UTC+8` and `GMT+8` are not IANA names,
    and POSIX reads their sign as the opposite of what nearly everyone means, so
    guessing would be worse than refusing.
    """
    if not tz:
        return tz
    return _zones_by_lowercase().get(tz.lower(), tz)


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
                # Normalised HERE so the row carries what `ZoneInfo` takes.
                # Accepting a spelling in the lint and then handing the sweep
                # something it cannot resolve would make the acceptance a lie:
                # it would fall back to UTC and fire at the wrong hour.
                tz=normalise_tz(str(row.get("tz") or "")),
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
        # CHECKED ON EVERY ROW, not only where the field means something.
        # `parse_user_schedules` decodes `dom` and `with` unconditionally
        # (`int(...)`, `dict(...)`), so a value it cannot decode raises — and the
        # validator, which only looked at `dom` for a monthly row and never
        # looked at `with` at all, called the file clean. A linter that grades a
        # narrower set than the parser reads is a linter that promises the parser
        # will succeed and does not check.
        dom_raw = row.get("dom")
        if dom_raw is not None and not isinstance(dom_raw, int):
            problems.append(f"{where}: `dom` must be a number, got {dom_raw!r}.")
        with_raw = row.get("with")
        if with_raw is not None and not isinstance(with_raw, dict):
            problems.append(
                f"{where}: `with` must be an object of values for the workflow, got {with_raw!r}."
            )
        # `or`, not a `.get` default: a JSON `null` has to mean what an omitted
        # key means. A page generator writes nulls for the fields it left
        # unset, and `.get(k, default)` only fires when the key is ABSENT — so
        # `"every": null` reached the check as `None`, was refused, and the row
        # was dropped, while omitting the same key was accepted. The parser
        # already spelled every one of these `or`; the validator did not, so the
        # two halves disagreed about the same file.
        every = row.get("every") or "daily"
        if every not in EVERY:
            problems.append(f"{where}: `every` is {every!r}; it must be one of {', '.join(EVERY)}.")
            continue
        n = row.get("n")
        if every == "minutes":
            if not isinstance(n, int) or n < 1:
                problems.append(f"{where}: `every: minutes` needs `n` — how many minutes apart.")
            elif 60 % n:
                # The bucket is `(minute // n) * n`, anchored to the top of the
                # hour, so only a divisor of 60 is honest. `n: 90` makes
                # `minute // 90` always 0 — "every 90 minutes" fires HOURLY, and
                # so does `n: 1440`. `n: 7` gives :00 :07 … :56 with a four
                # minute last bucket, which is nine fires an hour, not eight and
                # a half. Silently, in every case: the only way to notice the
                # cadence you did not ask for is to sit and watch a clock.
                problems.append(
                    f"{where}: `n` must divide 60 (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60), "
                    f"got {n}. For anything longer use `every: hourly` or `every: daily`."
                )
        elif n:
            problems.append(f"{where}: `n` applies only to `every: minutes`.")
        # `or ""`, the SAME spelling `parse_user_schedules` uses, because the two
        # halves have to agree about one file. `str(row.get("tz", ""))` turns a
        # JSON `null` into the string "None" — truthy, not a zone — so a page
        # that wrote `"tz": null` for "I did not pick one" had its row refused
        # and was told about a value nobody typed, while the parser next door
        # ran the same row happily. reference.md tells authors tz is optional,
        # and a generated page emits nulls for what it left out.
        tz = normalise_tz(str(row.get("tz") or ""))
        if tz and not _valid_tz(tz):
            # Nothing checked this, so a typo travelled all the way to `ZoneInfo`
            # — which raises `ValueError` for an absolute path or a traversal,
            # not the `ZoneInfoNotFoundError` the sweep was catching. One bad
            # zone took the WHOLE file down and the good rows in it stopped
            # firing, which is the failure this per-row linting exists to stop.
            problems.append(f"{where}: `tz` {tz!r} is not a known IANA time zone.")
        if every == "weekly" and row.get("dow") not in _DOW:
            problems.append(f"{where}: a weekly schedule needs `dow` ({', '.join(_DOW)}).")
        if every == "monthly":
            dom = row.get("dom", 0)
            # RANGE only. The type is graded once, above, on every row —
            # because the parser decodes `dom` on every row. Testing
            # `isinstance` here as well meant one mistake produced two
            # complaints: "must be a number" and "must be 1..31", about the same
            # `dom: "x"`. The cap counts what this returns, which is the defect
            # P38 fixed, and an author reading two messages looks for two
            # problems. A rule that sinks below an older one takes its job with
            # it rather than sitting beside it.
            if isinstance(dom, int) and not (1 <= dom <= 31):
                problems.append(f"{where}: `dom` must be 1..31, got {dom!r}.")
        if every in ("daily", "weekly", "monthly"):
            at = row.get("at") or "00:00"  # `null` means the same as omitted
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


def file_rows(raw: str) -> list[Any] | None:
    """The `schedules` list of a file, or None when the file is not that shape
    (not JSON, not an object, no list) — the one decode both readers share."""
    try:
        doc = json.loads(raw)
        rows = doc["schedules"] if isinstance(doc, dict) else None
    except Exception:
        return None
    return rows if isinstance(rows, list) else None


def declared_count(raw: str) -> int | None:
    """How many schedules this file DECLARES, or None when it is not that shape.

    Cheap by construction — the length of a list, not its contents — so a cap can
    be applied BEFORE the per-row parsing it exists to bound. `usable_rows` costs
    one `json.dumps` plus one `json.loads` plus a full validation per row, and a
    runaway file paid all of it and was then refused, every tick, on every pod,
    for as long as it stayed indexed.

    It also counts the right thing. `validate_user_schedules` emits SEVERAL
    strings for one bad row, so counting rows-plus-problems reported a file of
    400 as 1200 — refusing it against a cap it never reached, and telling the
    operator a number they could not reconcile with the file in front of them.

    `None` rather than 0 for an unreadable file: "I cannot count this" and "it
    declares nothing" are different answers, and only one of them means the cap
    is satisfied.
    """
    rows = file_rows(raw)
    return None if rows is None else len(rows)


def over_cap(raw: str, max_rows: int) -> str | None:
    """The sentence the sweep logs when a file declares more schedules than the
    deployment allows — and refuses the WHOLE file — or None when it is within
    the cap. One sentence, so the panel says exactly what the sweep will do."""
    declared = declared_count(raw)
    if declared is None or declared <= max_rows:
        return None
    return (
        f"declares {declared} schedules, over the limit of {max_rows} — "
        "none will run until it is reduced"
    )


def parse_row(i: int, raw: Any) -> tuple[UserSchedule | None, list[str]]:
    """ONE row: the parsed schedule, or the problems that refuse it — renumbered
    to the row's real position so the message points at the line the author has
    to fix rather than always at zero. The single reading the sweep and the
    panel share, so they cannot disagree about a row."""
    one = json.dumps({"schedules": [raw]})
    bad = validate_user_schedules(one)
    if bad:
        return None, [p.replace("schedules[0]", f"schedules[{i}]") for p in bad]
    try:
        (row,) = parse_user_schedules(one)
    except Exception as exc:
        # BELT AND BRACES, and the belt is the linter above. A decode that
        # raises here would take the file's GOOD rows with it — the opposite of
        # `usable_rows`'s whole promise — and the author would see nothing,
        # because the linter is the only thing that reaches them. Every known
        # case is linted now; this keeps the next unknown one costing its own
        # row only.
        return None, [f"schedules[{i}]: could not be read ({exc})."]
    return row, []


def usable_rows(raw: str) -> tuple[list[UserSchedule], list[str]]:
    """The rows that can be run, and what is wrong with the rest.

    Per ROW, not per file. A file that will not parse at all yields nothing —
    there is nothing to read. But once it parses, one mistyped row must cost
    that row only: whole-file rejection means a person edits one schedule,
    fat-fingers it, and the OTHER report they rely on stops arriving too, with
    nothing anywhere saying why.
    """
    rows = file_rows(raw)
    if rows is None:
        return [], validate_user_schedules(raw)
    good: list[UserSchedule] = []
    problems: list[str] = []
    for i, raw_row in enumerate(rows):
        row, bad = parse_row(i, raw_row)
        if row is None:
            problems.extend(bad)
        else:
            good.append(row)
    return good, problems


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


def utc_now() -> datetime:
    """The clock, naive UTC — a module attribute so a test can pin it."""
    return datetime.now(UTC).replace(tzinfo=None)


def in_zone(now_utc: datetime, tz: str) -> datetime:
    """`now` as the wall clock in `tz`, naive. An empty zone means UTC, which is
    the same rule the engineer-authored triggers use (`TriggerSweeper._local_now`)
    so the two engines cannot disagree about what "09:00" means.

    ONE function for the sweep that fires a row and the tool that saves it, so
    the "next run" the agent reports is computed on the clock the sweep will
    actually fire on.

    A zone that cannot be resolved falls back to UTC rather than raising, because
    taking down one page's whole file — every other row in it included — over a
    typo in a zone name is a worse answer than firing an hour out.

    THE FULL SET, not just "not found". `ZoneInfo` raises `ValueError` for an
    absolute path or a traversal (`"/absolute"`, `"../x"`) and `OSError` for a key
    long enough to reach the filesystem. Catching only `ZoneInfoNotFoundError` is
    what made a single bad row raise out of the loop and stop every good schedule
    in the same file — the exact outcome this fallback exists to prevent, and the
    opposite of the sweep's "one page's mistake costs that page only".

    `validate_user_schedules` lints `tz` too, so a bad zone should never get
    this far. Both, deliberately: the lint is what TELLS the author, and this is
    what keeps a miss from being fatal. Neither alone is enough.
    """
    if not tz:
        return now_utc
    try:
        return now_utc.replace(tzinfo=UTC).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning("user schedules: unusable time zone %r — using UTC", tz)
        return now_utc


class SchedulePolicy(Struct, frozen=True):
    """What THIS deployment does with a schedules file — handed to the agent's
    `save_schedules` so it can say so instead of guessing.

    `max_rows` is the same runaway guard the sweep applies (`server.max_page_schedules`),
    checked at save time so the refusal reaches the author rather than a log.
    `sweep_enabled` is whether the deploy runs scheduled work at all
    (`server.trigger_check_interval_sec` > 0): a saved file on a deploy with the
    sweep off is a file nothing will ever read, and the ONE thing the tool must
    not do is let the agent report that as "set up".
    """

    max_rows: int
    sweep_enabled: bool


#: Where an ITEM's own schedules live — beside its workflows, the folder the
#: agent's `save_workflow` already writes. `is_schedule_file` accepts both this
#: and a page's, and the sweep treats them alike: one rule, two declaration points.
ITEM_SCHEDULES_PATH = f"/{WORKSPACE_WORKFLOW_DIR}/{SCHEDULES_FILE}"


def describe_row(row: UserSchedule) -> str:
    """The row in words, for the reply the agent relays: `daily at 09:00
    Asia/Taipei`, `weekly on mon at 08:00 UTC`, `every 15 minutes`."""
    zone = row.tz or "UTC"
    if row.every == "minutes":
        return f"every {row.n} minutes ({zone})"
    if row.every == "hourly":
        return f"hourly ({zone})"
    if row.every == "weekly":
        return f"weekly on {row.dow} at {row.at} {zone}"
    if row.every == "monthly":
        return f"monthly on day {row.dom} at {row.at} {zone}"
    return f"daily at {row.at} {zone}"


def next_run_at(row: UserSchedule, now_utc: datetime, last_window: str) -> str:
    """When this row fires next as `YYYY-MM-DD HH:MM` in ITS zone, on the rule
    the sweep fires by — or `""` when it is due right now (the next sweep).

    The empty case is the one worth keeping distinct rather than rounding away:
    a missed window fires late, so a daily 09:00 saved at 10:00 runs within the
    minute (the catch-up rule the sweep's reference documents), and a reply or a
    panel that said "tomorrow" would contradict the manual it stands in for.
    """
    when = next_run(row.as_schedule(), in_zone(now_utc, row.tz), last_window)
    return "" if when is None else f"{when:%Y-%m-%d %H:%M}"


def describe_next_run(row: UserSchedule, at: str) -> str:
    """`next_run_at`'s answer as the sentence the agent relays."""
    if not at:
        return "on the next sweep (this period is already due and has not run yet)"
    return f"{at} {row.tz or 'UTC'}"


class ScheduleView(Struct):
    """One row of a schedules file as a PERSON (or the agent) should see it —
    what the sweep will do with it, not what was typed.

    Every row in the file is here, in file order, the refused ones included:
    a panel that dropped them could not rewrite the file minus one row without
    silently losing the others, and a person cannot fix a line they are not
    shown. `raw` is the row as written, for exactly that rewrite.

    `runnable` is THE verdict — will the sweep fire this row — and the next-run
    fields are filled only when it is true: a "next" on a row the sweep will
    skip is a report promised that never arrives. `next_run` is the English
    sentence the agent relays; `next_at` / `due_now` / `tz` are the pieces a
    panel localises, derived from the one `next_run_at` call.
    """

    index: int
    raw: Any
    """The row EXACTLY as written — whatever JSON value it was, an object or not
    — because a rewrite that dropped one row must put the others back untouched,
    and a substitute for a malformed one is a line the author never typed."""
    problems: list[str]
    run: str = ""
    describe: str = ""
    runnable: bool = False
    next_run: str = ""
    next_at: str = ""
    due_now: bool = False
    tz: str = "UTC"
    known: bool = False
    payload: dict[str, Any] = {}


def schedule_views(
    raw_text: str,
    *,
    offered: Collection[str],
    now_utc: datetime,
    last_window: Callable[[UserSchedule], str],
    max_rows: int | None = None,
    enabled: bool = True,
    indexed: bool = True,
) -> tuple[list[ScheduleView], list[str]]:
    """Every row of a schedules file, described the way the sweep reads it —
    same parser, same cap, same next-run rule, same ledger (`last_window`) —
    plus the file-level problems. ONE implementation of "what will the sweep do
    with this row", used by the agent's `save_schedules` reply and the panel's
    listing, and held to the sweep by `tests/api/test_schedules_route_parity.py`.

    A row is `runnable` only when EVERYTHING the sweep checks holds: the row
    parses, its `run` is one the item offers, the file is within the cap
    (`max_rows`, the sweep refuses the WHOLE file over it), the deployment runs
    scheduled work at all (`enabled`), and the sweep knows the file exists
    (`indexed` — it reads only what the index names). Each of those is a way a
    row silently never fires, so each is said here rather than rounded away.
    """
    rows = file_rows(raw_text)
    if rows is None:
        return [], validate_user_schedules(raw_text)

    file_problems: list[str] = []
    capped = over_cap(raw_text, max_rows) if max_rows is not None else None
    if capped is not None:
        file_problems.append(capped)

    views: list[ScheduleView] = []
    for i, raw in enumerate(rows):
        row, problems = parse_row(i, raw)
        if row is None:
            views.append(ScheduleView(index=i, raw=raw, problems=problems))
            continue
        known = row.run in offered
        runnable = known and capped is None and enabled and indexed
        at = next_run_at(row, now_utc, last_window(row)) if runnable else ""
        views.append(
            ScheduleView(
                index=i,
                raw=raw,
                # The cap is the FILE's problem (said once, above); each row's
                # own record stays clean and its `runnable` carries the verdict.
                problems=[],
                run=row.run,
                describe=describe_row(row),
                runnable=runnable,
                next_run=describe_next_run(row, at) if runnable else "",
                next_at=at,
                due_now=runnable and not at,
                tz=row.tz or "UTC",
                known=known,
                payload=row.payload,
            )
        )
    return views, file_problems


def last_window_lookup(spec: Any, item_id: str) -> Callable[[UserSchedule], str]:
    """A `last_window` resolver over the sweep's ledger for THIS item's own
    schedules file, or one that answers "never" when there is no ledger to ask
    (no spec, or a deploy whose sweep is off never registered the store).

    The lookup is a BLOCKING specstar read per distinct row. `schedule_views`
    calls it inline, so a caller on the event loop runs the whole
    `schedule_views(...)` under `asyncio.to_thread` — one hop for the file, not
    one per row.
    """
    if spec is None:
        return lambda _row: ""
    from .triggers import SpecstarTriggerStore

    store = SpecstarTriggerStore(spec)
    folder = ITEM_SCHEDULES_PATH.rsplit("/", 1)[0]
    cache: dict[str, str] = {}

    def _lookup(row: UserSchedule) -> str:
        key = trigger_id_for(item_id, folder, row)
        if key not in cache:
            try:
                cache[key] = store.last_window(key)
            except Exception:  # noqa: BLE001 — a listing, not a run; no ledger reads as "never"
                cache[key] = ""
        return cache[key]

    return _lookup
