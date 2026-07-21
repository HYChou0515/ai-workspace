"""#534 甲 — the deterministic half of "the same thing gets the same name".

Three pure functions, applied at READ time. Nothing here calls a model, clusters
a vector, or asks a human: a metric name, a period and a unit are surface forms
of things with an exact answer, and a rule that can be read and unit-tested beats
one that has to be reviewed.

The failure direction is chosen on purpose. Every rule below may FAIL TO MERGE
two spellings of one thing — that shows up as "only one deck mentions this
metric", which a human notices. None of them may MERGE two different things:
that produces a confident, wrong contradiction ("gross profit 1.2M vs gross
margin 35%"), which costs someone an investigation to discover is nonsense. When
in doubt, split.
"""

from __future__ import annotations

import pytest

from workspace_app.kb.graph.normalize import norm_metric, norm_unit, parse_period


class TestNormMetric:
    """The metric NAME. Surface noise only — never meaning."""

    @pytest.mark.parametrize(
        "a,b",
        [
            ("Revenue", "revenue"),  # case
            ("  Net   Income ", "Net Income"),  # whitespace runs + edges
            ("Ｒｅｖｅｎｕｅ", "Revenue"),  # full-width latin, common in CJK decks
            ("營　收", "營收"),  # ideographic space
            ("Revenue:", "Revenue"),  # trailing punctuation off a slide label
            ("Revenue (USD)", "Revenue"),  # a unit parenthetical is not the name
            ("Revenue（百萬）", "Revenue"),  # ditto, full-width brackets
            ("Gross-Margin", "Gross Margin"),  # hyphen as a word separator
        ],
    )
    def test_the_same_metric_written_two_ways_gets_one_key(self, a: str, b: str):
        assert norm_metric(a) == norm_metric(b)

    @pytest.mark.parametrize(
        "a,b",
        [
            ("毛利", "毛利率"),  # profit vs margin — an amount vs a percentage
            ("Revenue", "Deferred Revenue"),  # a qualifier changes the metric
            ("Revenue", "Revenue Growth"),
            ("營收", "Revenue"),  # translation is MEANING, not surface — alias table
        ],
    )
    def test_two_different_metrics_never_collapse(self, a: str, b: str):
        assert norm_metric(a) != norm_metric(b)

    def test_an_empty_name_stays_empty(self):
        assert norm_metric("   ") == ""


class TestParsePeriod:
    """The PERIOD. Structured, so it parses — and the parse is what gets compared,
    which is how "FY2024" and "2024年度" stop being different periods."""

    @pytest.mark.parametrize(
        "a,b",
        [
            ("2024", "2024年"),
            ("FY2024", "FY 2024"),
            ("Q3 2024", "2024 Q3"),
            ("Q3 2024", "2024年第三季"),
            ("H1 2024", "2024上半年"),
        ],
    )
    def test_one_period_written_two_ways_parses_the_same(self, a: str, b: str):
        assert parse_period(a) == parse_period(b)

    @pytest.mark.parametrize(
        "a,b",
        [
            ("2023", "2024"),  # the whole point — adjacent years are NOT the same
            ("Q1 2024", "Q2 2024"),
            ("Q1 2024", "2024"),  # a quarter is not its year
            ("H1 2024", "Q1 2024"),  # a half is not its first quarter
            ("FY2024", "2024"),  # a fiscal year need not align with the calendar
        ],
    )
    def test_two_different_periods_never_collapse(self, a: str, b: str):
        assert parse_period(a) != parse_period(b)

    def test_an_unparseable_period_keeps_its_own_literal_group(self):
        """ "去年同期" has no meaning without a document date we do not have. It
        forms its own group rather than being dropped (which would silently lose a
        real measurement) or guessed into a year (which would invent one)."""
        assert parse_period("去年同期") == parse_period("去年同期")
        assert parse_period("去年同期") != parse_period("2024")
        assert parse_period("去年同期") != parse_period("上個會計年度")

    def test_an_unparseable_period_still_ignores_pure_surface_noise(self):
        assert parse_period(" 去年同期 ") == parse_period("去年同期")

    def test_a_missing_period_is_its_own_thing_not_an_unparseable_one(self):
        """No period at all is a fact about the claim ("headcount: 340"), not a
        failure to read one, so it must not share a group with unreadable text."""
        assert parse_period("") == parse_period("   ")
        assert parse_period("") != parse_period("去年同期")


class TestNormUnit:
    """The UNIT. A small closed set with well-known spellings."""

    @pytest.mark.parametrize(
        "a,b",
        [
            ("USD", "usd"),
            ("USD", "$"),
            ("USD", "美元"),
            ("%", "％"),
            ("percent", "%"),
        ],
    )
    def test_one_unit_written_two_ways_gets_one_key(self, a: str, b: str):
        assert norm_unit(a) == norm_unit(b)

    @pytest.mark.parametrize("a,b", [("USD", "EUR"), ("USD", "%"), ("USD", "TWD")])
    def test_two_different_units_never_collapse(self, a: str, b: str):
        assert norm_unit(a) != norm_unit(b)

    def test_an_unknown_unit_keeps_its_own_literal_group(self):
        """An unrecognised unit is not forced into a known one — it compares only
        against the identical spelling, so an unseen currency splits rather than
        silently becoming dollars."""
        assert norm_unit("KRW") == norm_unit("krw")
        assert norm_unit("KRW") != norm_unit("USD")

    def test_an_ambiguous_currency_symbol_is_not_guessed(self):
        """A bare "元" is TWD, CNY or JPY depending on whose deck it is, and the
        claim does not say. Guessing would compare one currency against another and
        report the gap as a contradiction, so it stays literal — the same treatment
        an unknown unit gets, for the same reason."""
        assert norm_unit("元") != norm_unit("TWD")
        assert norm_unit("元") != norm_unit("CNY")
        assert norm_unit("元") == norm_unit("元")
