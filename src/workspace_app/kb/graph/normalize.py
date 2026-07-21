"""#534 甲 — deterministic naming: the same thing gets the same key.

A metric name, a period and a unit are surface forms of things that HAVE an exact
answer, so they are settled by rules that can be read and unit-tested rather than
by a model, a vector neighbourhood or a review queue. Those are reserved for the
one question rules cannot answer — whether "營收" and "Revenue" are the same
metric — which is the alias table, a later step.

**Applied at READ time, never stored.** The raw surface (what the slide actually
said) is the only source of truth; these functions derive a comparison key from
it on demand. Storing a derived key would freeze whichever version of the rules
was current when the row was written, and improving a rule would then leave older
rows silently on the old one — visible nowhere, and repairable only by a backfill
whose omission also shows up nowhere. Deriving on read means there is exactly one
rule in force at any moment. It costs a parse per row, measured at ~1.6 µs and
under 0.05 µs once the handful of distinct surfaces are cached — noise beside the
group-by that already has to load every row.

**Every rule here may fail to merge; none of them may merge wrongly.** A failure
to merge reads as "only one deck mentions this metric", which a person notices
and can fix with an alias. A wrong merge produces a confident, false
contradiction — "gross profit 1.2M vs gross margin 35%" — which costs an
investigation to discover is nonsense, and erodes trust in every other row. When
a rule is unsure, it splits.
"""

from __future__ import annotations

import re
import unicodedata

# Punctuation that decorates a slide label rather than naming anything:
# "Revenue:" / "Revenue —" / "(Revenue)". Kept deliberately narrow — a character
# that could carry meaning inside a name (%, /) is NOT here.
_TRIM_PUNCT = " \t\r\n:：;；.。,,、-—–_*#•·()（）[]【】{}"

# A trailing parenthetical on a metric label is a unit or a scale, not part of the
# name: "Revenue (USD)", "營收(百萬)". The unit travels in its own field.
_PARENTHETICAL = re.compile(r"[(（\[【][^)）\]】]*[)）\]】]")

# A hyphen inside a compound label separates words: "Gross-Margin" is "gross margin".
_WORD_SEP = re.compile(r"[-–—_/]+")
_SPACE_RUN = re.compile(r"\s+")

# A space between two CJK characters separates nothing — CJK is not written with
# word spaces, so "營 收" is one word typed loosely (or an ideographic space that
# NFKC turned into an ordinary one). Removed only BETWEEN ideographs, so a Latin
# name keeps the spaces that do carry meaning ("Net Income" stays two words).
_CJK_GAP = re.compile(r"(?<=[\u3400-\u9fff\uf900-\ufaff])\s+(?=[\u3400-\u9fff\uf900-\ufaff])")


def _fold_width(text: str) -> str:
    """Full-width → half-width (NFKC), so a CJK deck's "Ｒｅｖｅｎｕｅ" and an
    English deck's "Revenue" are one metric. NFKC also folds the ideographic
    space and full-width brackets, which is why it runs before everything else."""
    return unicodedata.normalize("NFKC", text)


def norm_metric(metric: str) -> str:
    """The grouping key for a metric NAME — surface noise removed, meaning intact.

    Removes: width variants, case, whitespace runs, decorative punctuation, a
    trailing unit parenthetical, and word-separating hyphens. Does NOT touch
    anything that could change which metric is meant: a qualifier ("Deferred
    Revenue"), a suffix that turns an amount into a ratio ("毛利" vs "毛利率"), or
    a translation ("營收" vs "Revenue"). Those are different keys here on purpose —
    equating them is a judgement about meaning, which belongs to the alias table.
    """
    text = _fold_width(metric)
    text = _PARENTHETICAL.sub(" ", text)
    text = _WORD_SEP.sub(" ", text)
    text = _SPACE_RUN.sub(" ", text).strip(_TRIM_PUNCT)
    text = _CJK_GAP.sub("", text)
    return _SPACE_RUN.sub(" ", text).strip().casefold()


# ── period ───────────────────────────────────────────────────────────

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_FISCAL = re.compile(r"\bFY\b|FY(?=\d)|年度|會計年度|財年", re.IGNORECASE)
_QUARTER = re.compile(r"Q\s*([1-4])(?!\d)|第\s*([一二三四1-4])\s*季", re.IGNORECASE)
_HALF = re.compile(r"H\s*([12])(?!\d)|(上|下)半年?", re.IGNORECASE)
_CJK_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4}

# The parse result is a tuple, so equality IS the comparison — no separate
# "are these the same period" function to keep in step with the parser.
Period = tuple[object, ...]

_NO_PERIOD: Period = ("none",)


def parse_period(period: str) -> Period:
    """Parse a period surface into a comparable key.

    Recognises a calendar or fiscal year, a quarter and a half, in the spellings
    that actually turn up on decks ("2024", "FY2024", "2024年度", "Q3 2024",
    "2024年第三季", "H1 2024", "2024上半年").

    Three outcomes, deliberately distinct:

    * a parse — compares equal to any other spelling of the same period;
    * ``("none",)`` for an absent period — a FACT about the claim ("headcount:
      340" has no period), not a failure;
    * ``("raw", <folded text>)`` for something we cannot read — "去年同期" means
      nothing without a document date we do not have. It groups only with the
      identical text. Dropping it would silently lose a real measurement; guessing
      a year would invent one.

    A fiscal year is NOT merged with the calendar year of the same number: they
    coincide only for companies whose fiscal year happens to align, and merging
    them would compare figures from different twelve-month windows.
    """
    text = _SPACE_RUN.sub(" ", _fold_width(period)).strip()
    if not text:
        return _NO_PERIOD
    year_match = _YEAR.search(text)
    year = int(year_match.group(1)) if year_match else None
    fiscal = bool(_FISCAL.search(text))
    if year is None:
        return ("raw", text.casefold())
    quarter = _QUARTER.search(text)
    if quarter:
        raw = quarter.group(1) or quarter.group(2)
        index = _CJK_DIGIT.get(raw) if raw in _CJK_DIGIT else int(raw)
        return ("quarter", year, index, fiscal)
    half = _HALF.search(text)
    if half:
        index = int(half.group(1)) if half.group(1) else (1 if half.group(2) == "上" else 2)
        return ("half", year, index, fiscal)
    return ("year", year, fiscal)


# ── unit ─────────────────────────────────────────────────────────────

# A small closed set: the spellings one deck uses for what another deck writes
# differently. Only entries whose equivalence is a FACT about notation belong
# here — "$" is how people write USD. Anything requiring a rate (USD↔EUR) or a
# judgement is not a spelling, and is not here.
_UNIT_ALIASES = {
    "usd": "USD",
    "us$": "USD",
    "$": "USD",
    "美元": "USD",
    "美金": "USD",
    "eur": "EUR",
    "€": "EUR",
    "歐元": "EUR",
    "twd": "TWD",
    "ntd": "TWD",
    "nt$": "TWD",
    "台幣": "TWD",
    "新台幣": "TWD",
    # NOT here: a bare "元". It is TWD on a Taiwanese deck, CNY on a mainland one
    # and JPY on a Japanese one, and nothing in a claim tells us which. Folding it
    # into any of them would compare one currency's figures against another's and
    # call the gap a contradiction — the one failure this module must not have. It
    # stays literal, so "元" only ever compares against "元".
    "jpy": "JPY",
    "¥": "JPY",
    "日圓": "JPY",
    "日元": "JPY",
    "cny": "CNY",
    "人民幣": "CNY",
    "rmb": "CNY",
    "%": "%",
    "percent": "%",
    "pct": "%",
    "百分比": "%",
    "趴": "%",
}


def norm_unit(unit: str) -> str:
    """The comparison key for a UNIT.

    A known spelling maps to its canonical form; an unknown one keeps its own
    folded text and therefore compares only against the identical spelling. That
    is the safe direction: an unrecognised currency splitting off is visible
    ("this metric only appears in one deck"), whereas folding it into a known unit
    would compare won against dollars and call the difference a contradiction.

    Currency CONVERSION is not normalisation and never happens here — USD and EUR
    are different units, and a claim in each is two measurements, not a conflict.
    """
    text = _SPACE_RUN.sub("", _fold_width(unit)).strip(_TRIM_PUNCT).casefold()
    if not text:
        return ""
    return _UNIT_ALIASES.get(text, text)
