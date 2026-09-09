"""Schedules a PAGE declares, as opposed to ones an engineer writes.

The profile's `triggers.json` is authored once by whoever builds the app. This
is the other half: a file a WUI writes into its own folder, so a domain expert
can say "every Monday at 09:00, build my report" without anyone editing the
repo.

Two properties carry the whole design:

* **The declaration is data and the state is the platform's.** A page writes the
  file with `writeFile`, which REPLACES — so pressing save five times is one
  schedule, not five, and there is no idempotency key to get wrong.
* **The lease key comes from the CONTENT, never from an id the page chose.** A
  page that regenerates a random id on every save would otherwise look like a
  brand-new schedule each time, and the window ledger would reset — firing again
  for a window it had already fired for, and sending the mail twice.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime

import pytest

from workspace_app.workflow.triggers import _DOW, fire_window, is_due, period_target
from workspace_app.workflow.user_schedules import (
    UserSchedule,
    declared_count,
    parse_user_schedules,
    trigger_id_for,
    usable_rows,
    validate_user_schedules,
)

ITEM = "rca:i1"
FOLDER = "/scrap-review"


def _file(*rows: dict) -> str:
    import json

    return json.dumps({"schedules": list(rows)})


DAILY = {"every": "daily", "at": "09:00", "run": "build-report", "with": {"line": "A"}}
POLLER = {"every": "minutes", "n": 5, "run": "check-arrivals"}


# ── the format ───────────────────────────────────────────────────────────────


def test_reads_the_rows_a_page_wrote():
    rows = parse_user_schedules(_file(DAILY, POLLER))

    assert [r.run for r in rows] == ["build-report", "check-arrivals"]
    assert rows[0].at == "09:00"
    assert rows[0].payload == {"line": "A"}
    assert rows[1].n == 5


def test_an_unreadable_file_is_a_problem_not_a_crash():
    """A page writes this file, and a page is written by an LLM. A malformed one
    must report itself the way a lint does — the same contract `validate_triggers`
    already keeps — because raising here would take down the sweep for every
    OTHER item too."""
    problems = validate_user_schedules("not json at all")

    assert problems and "could not be read" in problems[0].lower()


def test_a_row_missing_its_workflow_is_named_not_dropped():
    problems = validate_user_schedules(_file({"every": "daily", "at": "09:00"}))

    assert any("run" in p for p in problems)


def test_a_bad_period_is_named_with_what_is_allowed():
    """The reader cannot open a console. "invalid" tells them nothing; the list
    of words that work tells them everything."""
    problems = validate_user_schedules(_file({**DAILY, "every": "fortnightly"}))

    assert any("fortnightly" in p and "daily" in p for p in problems)


def test_minutes_needs_a_count_and_the_others_must_not_have_one():
    assert any("n" in p for p in validate_user_schedules(_file({"every": "minutes", "run": "x"})))
    assert any("n" in p for p in validate_user_schedules(_file({**DAILY, "n": 5})))


def test_a_minutes_interval_the_hour_cannot_hold_is_refused():
    """The bucket is `(minute // n) * n`, anchored to the top of each hour. That
    is exact for a divisor of 60 and a lie for anything else:

    * `n: 90` → `minute // 90` is always 0, so "every 90 minutes" fires HOURLY.
      So does `n: 1440`, which is how somebody spells "daily" in minutes.
    * `n: 7` → buckets at :00 :07 … :56, and the last one is four minutes long,
      so "every 7 minutes" is nine fires an hour rather than eight and a half.

    Neither says anything. The page gets a cadence it did not ask for and the
    only way to notice is to watch a clock, so this is refused at the door with
    the alternative named.
    """
    # `n: 60` is NOT in this list: it divides 60, so its bucket is the top of
    # each hour and "every 60 minutes" means exactly what it says. Redundant
    # with `every: hourly`, not wrong.
    for bad in (90, 1440, 7, 45):
        problems = validate_user_schedules(_file({"every": "minutes", "n": bad, "run": "x"}))
        assert problems, f"n={bad} was accepted"
        assert "60" in problems[0], f"n={bad} was refused without saying what would work"


def test_every_interval_that_divides_the_hour_is_accepted():
    """The positive control. A rule that refused every `n` would pass the test
    above and delete the feature."""
    for good in (1, 2, 3, 5, 10, 15, 20, 30):
        assert validate_user_schedules(_file({"every": "minutes", "n": good, "run": "x"})) == [], (
            f"n={good} should be fine"
        )


def test_a_zone_that_is_not_a_zone_is_refused():
    """Nothing validated `tz` at all, so a typo reached `ZoneInfo` — which raises
    `ValueError` for an absolute path or a traversal, not the
    `ZoneInfoNotFoundError` the sweep catches. One bad zone took the WHOLE file
    down and the valid rows in it never fired, which is the exact thing this
    file's per-row linting exists to prevent."""
    for bad in ("/absolute", "../x", "Not/A/Zone", "\x00"):
        problems = validate_user_schedules(_file({**DAILY, "tz": bad}))
        assert problems, f"tz={bad!r} was accepted"
        assert "tz" in problems[0], f"tz={bad!r} was refused without naming the field"


def test_a_zone_written_in_the_wrong_case_still_works():
    """`utc` and `asia/taipei` are how people type these, and IANA names are
    case-sensitive only by convention. Refusing them turns "the report arrives an
    hour out" into "the report stops", which is the worse of the two failures —
    a wrong time gets noticed, an absent report gets noticed weeks later, and
    this file's own design note says so.

    `UTC+8` and `GMT+8` are NOT normalised: they are not IANA names at all, and
    in POSIX the sign means the opposite of what almost everyone intends. Guessing
    there would be worse than refusing.
    """
    for spelled in ("utc", "asia/taipei", "ASIA/TAIPEI", "Europe/berlin"):
        assert validate_user_schedules(_file({**DAILY, "tz": spelled})) == [], spelled

    for nonsense in ("UTC+8", "GMT+8", "local"):
        assert validate_user_schedules(_file({**DAILY, "tz": nonsense})), nonsense


def test_a_normalised_zone_is_the_one_the_schedule_runs_in():
    """Accepting the spelling is only half of it — the parsed row has to carry a
    zone `ZoneInfo` will take, or the sweep falls back to UTC and the acceptance
    was a lie."""
    from zoneinfo import ZoneInfo

    row = parse_user_schedules(_file({**DAILY, "tz": "asia/taipei"}))[0]

    assert ZoneInfo(row.tz)


def test_a_real_zone_is_accepted():
    """The control. Refusing every zone would pass the test above and break every
    schedule that names one."""
    for good in ("Asia/Taipei", "UTC", "Europe/Berlin", ""):
        assert validate_user_schedules(_file({**DAILY, "tz": good})) == [], good


def test_a_valid_file_has_nothing_to_say():
    assert validate_user_schedules(_file(DAILY, POLLER)) == []


# ── the lease key ────────────────────────────────────────────────────────────


def test_the_same_declaration_always_gets_the_same_key():
    """The reported failure this prevents: a page regenerates a random row id on
    every save, so the same logical schedule looks new, the window ledger resets,
    and it fires a second time for a day it had already fired for."""
    a = parse_user_schedules(_file(DAILY))[0]
    b = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, a) == trigger_id_for(ITEM, FOLDER, b)


def test_two_identical_rows_in_one_file_are_one_schedule():
    """Nothing wants to run the same thing twice at the same moment. Colliding on
    the key means the CAS lease lets exactly one of them through — duplication
    becomes structurally impossible rather than something a rule has to police."""
    rows = parse_user_schedules(_file(DAILY, DAILY))

    assert trigger_id_for(ITEM, FOLDER, rows[0]) == trigger_id_for(ITEM, FOLDER, rows[1])


@pytest.mark.parametrize(
    "change",
    [
        {"at": "10:00"},  # when
        {"run": "other-report"},  # what
        {"with": {"line": "B"}},  # with what
        {"every": "weekly", "dow": "mon"},  # how often
        {"tz": "Asia/Taipei"},  # 09:00 where
    ],
)
def test_changing_anything_that_matters_gets_a_new_key(change: dict):
    """Each of these is a different piece of work. Sharing a key with the old one
    would let the ledger say "already fired today" about something that has never
    run at all."""
    before = parse_user_schedules(_file(DAILY))[0]
    after = parse_user_schedules(_file({**DAILY, **change}))[0]

    assert trigger_id_for(ITEM, FOLDER, before) != trigger_id_for(ITEM, FOLDER, after)


def test_the_same_declaration_in_another_folder_is_another_schedule():
    """Two pages in one item may legitimately want the same report at the same
    time. They are different schedules and must not share a lease."""
    row = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, row) != trigger_id_for(ITEM, "/other", row)


def test_the_same_declaration_in_another_item_is_another_schedule():
    row = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, row) != trigger_id_for("rca:i2", FOLDER, row)


def test_the_key_ignores_the_order_of_the_payload():
    """`{a, b}` and `{b, a}` are the same payload. A key that disagreed would let
    a page fire twice by re-serialising its own file."""
    one = parse_user_schedules(_file({**DAILY, "with": {"a": 1, "b": 2}}))[0]
    two = parse_user_schedules(_file({**DAILY, "with": {"b": 2, "a": 1}}))[0]

    assert trigger_id_for(ITEM, FOLDER, one) == trigger_id_for(ITEM, FOLDER, two)


def test_the_key_says_where_it_came_from():
    """An operator reading the window ledger sees raw keys. One that names the
    item and the source is one they can act on; a bare hash is not."""
    row = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, row).startswith(f"wui:{ITEM}:")


# ── finer than a day (Q7: the floor was removed) ─────────────────────────────


def test_a_minutes_schedule_buckets_by_its_own_interval():
    """A poller is the one thing that legitimately runs often, and it is always
    ONE row — the fan-out lives inside the workflow. So the window has to be able
    to be smaller than a day, or an arrival check can only run once a day."""
    row = UserSchedule(every="minutes", n=5, run="check-arrivals")

    at_02 = fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 2))
    at_04 = fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 4))
    at_07 = fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 7))

    assert at_02 == at_04  # same five-minute bucket
    assert at_02 != at_07  # the next one


def test_an_hourly_schedule_buckets_by_the_hour():
    row = UserSchedule(every="hourly", run="check-arrivals")

    assert fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 2)) != fire_window(
        row.as_schedule(), datetime(2026, 9, 5, 10, 2)
    )


def test_a_daily_schedule_still_buckets_by_the_day():
    """The existing vocabulary must not move. `window_key` is shared with
    `send_notification`'s per-window fingerprint, so a "daily" schedule and a
    "daily" notify have to keep bucketing identically."""
    row = UserSchedule(every="daily", at="09:00", run="build-report")

    assert fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 30)) == "2026-09-05"


def test_a_sub_daily_period_targets_its_own_bucket_not_midnight():
    """Asserted on `period_target` directly, because `is_due` cannot see it: the
    default `at` is 00:00, which is always already past, so a branch returning
    "today at 00:00" and one returning the bucket's start both make `is_due`
    say yes. A mutation proved the point — replacing this branch changed no test
    until this one existed."""
    row = UserSchedule(every="minutes", n=5, run="check-arrivals").as_schedule()

    assert period_target(row, datetime(2026, 9, 5, 9, 7)) == datetime(2026, 9, 5, 9, 5)
    assert period_target(
        UserSchedule(every="hourly", run="x").as_schedule(), datetime(2026, 9, 5, 9, 7)
    ) == datetime(2026, 9, 5, 9, 0)


def test_a_wall_time_on_a_repeating_period_is_refused():
    """`every: minutes` plus `at: 09:00` is two answers to one question. Refused
    at declaration, so `period_target` has exactly one meaning for these."""
    problems = validate_user_schedules(
        _file({"every": "minutes", "n": 5, "run": "x", "at": "09:00"})
    )

    assert any("at" in p for p in problems)


def test_a_poller_is_due_again_in_the_next_bucket_but_not_the_same_one():
    row = UserSchedule(every="minutes", n=5, run="check-arrivals").as_schedule()
    now = datetime(2026, 9, 5, 9, 2)
    window = fire_window(row, now)

    assert is_due(row, now, last_window="") is True
    assert is_due(row, now, last_window=window) is False
    assert is_due(row, datetime(2026, 9, 5, 9, 7), last_window=window) is True


def test_the_declared_count_is_how_many_schedules_there_are():
    """The cap has to count SCHEDULES. `validate_user_schedules` emits several
    strings for one bad row — a single `{"every":"monthly","dom":99,"at":"9am"}`
    yields three — so counting rows-plus-problems reports a file of 400 as 1200,
    refuses it against a cap of 1000 that it never reached, and tells the
    operator it "declares 1200 schedules".

    Cheap on purpose: it reads the list's length, not its contents, so the cap
    can be applied BEFORE the per-row parsing it exists to bound.
    """
    bad = {"every": "monthly", "dom": 99, "at": "9am"}
    assert len(validate_user_schedules(_file(bad))) > 1, "this row must yield several problems"

    assert declared_count(_file(DAILY, POLLER)) == 2
    assert declared_count(_file(bad, bad, bad)) == 3
    assert declared_count(_file()) == 0


def test_a_file_that_is_not_a_schedules_file_declares_nothing_countable():
    """`None`, not 0 — "unreadable" and "an empty list" are different answers,
    and only one of them means the cap has been satisfied."""
    assert declared_count("not json at all") is None
    assert declared_count('{"schedules": "nope"}') is None
    assert declared_count("[]") is None


# --- what the shipped docs promise, the DSL must accept -----------------------


def _fenced_schedule_examples(text: str) -> list[str]:
    """Every ``{ "schedules": [...] }`` literal in a doc's fenced code blocks.

    Pulled out of the prose rather than duplicated into the test, so the thing
    asserted IS the thing shipped. A copy would pass forever while the doc drifted.
    """
    out: list[str] = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL):
        m = re.search(r"\{\s*\n?\s*schedules:\s*\[.*?\n\s*\]\s*\n?\s*\}", block, re.DOTALL)
        if m is None:
            continue
        # The docs show it as a JS object literal — bare keys, so quote them.
        js = m.group(0)
        js = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', js)
        js = re.sub(r",(\s*[}\]])", r"\1", js)  # no trailing commas
        out.append(js)
    return out


def test_the_docs_only_promise_schedules_the_dsl_can_express() -> None:
    """A doc example that does not validate is a defect in the product.

    The product here IS the documentation: a page author never reads this
    repo, and an LLM writing their page reads the skill. So an example the
    engine would refuse is not a typo — it is a feature the reader is told
    exists, writes down, and then does not get. And the refusal lands in a
    SERVER log, which is the one place the author cannot see.

    Asserted against the shipped file, so prose and engine cannot drift.
    """
    ref = pathlib.Path("sample-skills/wui/reference.md").read_text(encoding="utf-8")
    examples = _fenced_schedule_examples(ref)
    assert examples, "no schedules example found — the extractor stopped matching the doc"

    for js in examples:
        problems = validate_user_schedules(js)
        assert not problems, f"reference.md shows a schedule the engine refuses: {problems}"


def test_no_shipped_doc_offers_a_dow_the_engine_does_not_know() -> None:
    """`dow` takes ONE day. "weekdays" is not one of them.

    The tempting sentence is "weekdays at nine", because that is how people say
    it — and the DSL cannot express it in a row. An LLM handed that sentence
    writes `dow: "weekdays"`, the row is dropped, and the page's author sees a
    schedule that simply never runs. Five rows is the answer, and the docs have
    to say so rather than implying one will do.
    """
    for name in ("SKILL.md", "reference.md"):
        text = pathlib.Path("sample-skills/wui", name).read_text(encoding="utf-8")
        for word in re.findall(r'dow"?\s*:\s*"([a-z]+)"', text):
            assert word in _DOW, f"{name} offers dow={word!r}, which the engine refuses"
        assert "weekdays at" not in text, (
            f"{name} offers 'weekdays at ...', which `every: weekly` cannot express in one row"
        )


def test_a_null_tz_means_the_default_not_a_rejected_row() -> None:
    """`"tz": null` is what a generated page writes for "I did not choose one".

    reference.md tells the author `tz` is optional and defaults to UTC, and an
    LLM writing the page emits nulls for the fields it left out. The validator
    read it through `str(...)`, which turns `None` into the STRING `"None"` —
    truthy, and not a zone — so the row was refused and the message named a
    value the author never typed.

    `parse_user_schedules` was always null-tolerant (`row.get("tz") or ""`), so
    the two halves of this module disagreed about the same file: one would run
    the row, the other refused it. Refusing is the worse half — it turns "the
    report arrives an hour out" into "the report stops", which is the trade
    `normalise_tz`'s own docstring says it exists to avoid.
    """
    for absent in (None, 0, False):
        raw = _file({"every": "daily", "at": "09:00", "run": "r", "tz": absent})
        assert validate_user_schedules(raw) == [], f"tz={absent!r} was refused"
        assert parse_user_schedules(raw)[0].tz == "", f"tz={absent!r} did not default to UTC"

    # The control: a zone that is genuinely wrong is still refused, and the
    # message names what the author actually wrote.
    bad = _file({"every": "daily", "at": "09:00", "run": "r", "tz": "Asia/Taipeii"})
    problems = validate_user_schedules(bad)
    assert problems and "Asia/Taipeii" in problems[0]


@pytest.mark.parametrize("field", ["every", "at", "run", "dow", "dom", "tz", "n", "with"])
def test_writing_null_for_a_field_means_the_same_as_leaving_it_out(field: str) -> None:
    """The CLASS, not the one field that was reported.

    `.get(key, default)` fires only when the key is ABSENT, so every field read
    that way answered `None` for an explicit `null` and something else for an
    omitted key — two spellings of "I did not set this" that the validator
    graded differently. A page generator writes nulls for what it left unset,
    so the shape a real author produces is the one that was refused.

    Asserted over every field rather than the one that was found: `tz` was
    reported, and `every` and `at` had the same defect for the same reason.
    Fixing the instance and leaving the class is how this comes back.

    `run` is expected to be refused BOTH ways — it has no default, and a
    schedule with no workflow to start is genuinely unusable. That is the
    control: the property is "the two agree", not "everything is accepted".
    """
    base = {"every": "daily", "at": "09:00", "run": "r"}
    omitted = {k: v for k, v in base.items() if k != field}
    nulled = {**base, field: None}

    assert validate_user_schedules(_file(omitted)) == validate_user_schedules(_file(nulled)), (
        f"`{field}: null` is graded differently from omitting `{field}`"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("dom", "x"), ("dom", "3"), ("with", ["a", "b"]), ("with", "not a dict")],
)
def test_a_row_the_validator_calls_clean_can_never_make_the_parser_raise(
    field: str, value: object
) -> None:
    """The half of "the two must agree about one file" that RAISES.

    `validate_user_schedules` only looks at `dom` when `every == "monthly"`, and
    never looks at `with` at all — but `parse_user_schedules` decodes both on
    every row (`int(row.get("dom") or 0)`, `dict(row.get("with") or {})`). So a
    file the linter calls clean makes `usable_rows` raise `ValueError`.

    The raise escapes before `return good, problems`, so it costs the file's
    GOOD rows too — the exact thing `usable_rows`' own docstring promises
    against: "one mistyped row must cost that row only". And the author is told
    nothing, because the linter is the only thing that reaches them and it is
    empty.

    `"with": ["a", "b"]` is a shape an LLM page generator plausibly emits.
    """
    raw = _file({"every": "daily", "at": "09:00", "run": "r", field: value}, DAILY)

    problems = validate_user_schedules(raw)
    rows, from_usable = usable_rows(raw)  # must not raise

    assert problems, f"`{field}: {value!r}` is refused by the parser and linted by nothing"
    assert [r.run for r in rows] == ["build-report"], (
        "the good row beside it was lost — one row's mistake cost the whole file"
    )
    assert from_usable, "the row was dropped with nothing said about it"


def test_a_row_the_parser_cannot_read_costs_only_its_own_row(monkeypatch) -> None:
    """The belt, pinned independently of which shapes currently reach it.

    The linter now grades every field the parser decodes, so no input I can
    construct still makes `parse_user_schedules` raise — removing the guard in
    `usable_rows` leaves the shape-based tests green. That is exactly the
    argument for pinning the PROPERTY rather than a shape: the guard exists for
    the parser change nobody has made yet, and a test that depends on today's
    broken input stops holding the day that input is linted.

    The property: a row the parser cannot read is dropped WITH a complaint, and
    the good rows beside it still run. Before, the raise escaped before
    `return good, problems` and took the whole file — the opposite of this
    function's own promise.
    """
    import workspace_app.workflow.user_schedules as mod

    real = mod.parse_user_schedules
    calls: list[int] = []

    def _raises_on_the_first_row(raw):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("a decode nobody linted for")
        return real(raw)

    monkeypatch.setattr(mod, "parse_user_schedules", _raises_on_the_first_row)

    rows, problems = usable_rows(_file(DAILY, POLLER))

    assert [r.run for r in rows] == [POLLER["run"]], "the unreadable row took the good row with it"
    assert any("could not be read" in p for p in problems), (
        "the row vanished with nothing said about it"
    )
