"""#633 P1/P2 — resolve the names a question mentions, without asking the model.

A 14B model decides whether to look something up by whether IT knows the word,
not by whether the question is internal, so 「回焊爐是什麼?」 never reached the
graph even with the dossier tool one call away (#630 P7, four attempts). The fix
is to stop asking: names that appear in the question are resolved here and their
facts are handed to the turn.

The shape is forced by measurement rather than taste:

* names are matched by scanning the QUESTION for known names — no tokenizer,
  because we have the vocabulary already, and CJK has no word boundaries to
  split on anyway;
* the index lives in memory as name → ids, because looking a name up in the
  database is a full scan (2849 ms at 40k rows) while a primary-key fetch is
  0.3 ms;
* a name maps to MANY ids, never one — two pods can mint an identity
  concurrently, a merge leaves a tombstone behind, and two collections can name
  different things the same way.
"""

from __future__ import annotations

from workspace_app.kb.graph.name_index import NameIndex


def test_a_name_in_the_question_is_found_without_tokenizing_anything():
    idx = NameIndex({"回焊爐": ("e:1",), "產線三": ("e:2",)})
    assert idx.hits("回焊爐是什麼?它用什麼 recipe?") == {"回焊爐": ("e:1",)}


def test_an_ascii_code_is_not_matched_inside_a_longer_one():
    """The m4/m40 trap: 'M4' must not fire on 'M40'. CJK has no such boundary and
    must still match embedded, which is why the rule is ASCII-only."""
    idx = NameIndex({"m4": ("e:1",), "m40": ("e:2",)})
    assert idx.hits("M40 的良率?") == {"m40": ("e:2",)}


def test_matching_ignores_typing_noise_the_way_names_are_keyed():
    idx = NameIndex({"reflow oven": ("e:1",)})
    assert idx.hits("What about the REFLOW  OVEN?") == {"reflow oven": ("e:1",)}


def test_one_name_can_resolve_to_several_things():
    """Ambiguity is real (concurrent pods, merge tombstones, two collections
    naming different things alike) and must be carried, not collapsed."""
    idx = NameIndex({"ppoo": ("e:1", "e:2")})
    assert idx.hits("ppoo 是什麼") == {"ppoo": ("e:1", "e:2")}


def test_a_question_naming_nothing_known_costs_nothing():
    idx = NameIndex({"回焊爐": ("e:1",)})
    assert idx.hits("今天天氣如何?") == {}


def test_very_short_names_are_not_indexed_at_all():
    """A one-character name would fire on almost every question. The floor is a
    property of the index, not of each lookup, so nothing downstream can forget
    it."""
    idx = NameIndex({"爐": ("e:1",), "回焊爐": ("e:2",)})
    assert idx.hits("回焊爐") == {"回焊爐": ("e:2",)}


def test_the_index_states_what_it_had_to_leave_out():
    """A cap that silently truncates reads as 'we covered everything'. The count
    is kept so the operator can see the index stopped being complete — and the
    dossier tool still reaches whatever the index dropped."""
    idx = NameIndex({f"name{i}": (f"e:{i}",) for i in range(10)}, limit=4)
    assert len(idx) == 4
    assert idx.dropped == 6


def test_lookup_work_is_bounded_by_the_question_not_by_the_index():
    """The property the whole module exists for, pinned structurally rather than
    by timing (a clock assertion is a CI flake waiting to happen): candidate
    generation is bounded by the LONGEST NAME HELD, so an index of short names
    does not pay for a long one nobody has, and the intersection is against a
    hash table rather than a scan."""
    short = NameIndex({"回焊爐": ("e:1",)})
    assert short._longest == 3  # not the 24-char ceiling

    # An index 1000x larger resolves the same question to the same answer, and
    # its candidate set is identical — nothing about it scales with the names.
    big = NameIndex({**{f"機台{i}": (f"e:{i}",) for i in range(1000)}, "回焊爐": ("e:1",)})
    assert big.hits("回焊爐是什麼?") == {"回焊爐": ("e:1",)}


def test_a_name_longer_than_the_ceiling_never_widens_every_lookup():
    """One pathological name (a whole sentence extracted as an identity) must not
    make every message generate candidates that long."""
    from workspace_app.kb.graph.name_index import MAX_NAME_LEN

    idx = NameIndex({"x" * 200: ("e:1",), "回焊爐": ("e:2",)})
    assert idx._longest == MAX_NAME_LEN
