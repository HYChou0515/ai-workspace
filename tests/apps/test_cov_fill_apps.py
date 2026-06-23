"""Characterization tests filling coverage gaps in the apps package.

Covers ``profiles.list_profiles`` missing-app fallback and
``seeding.case_from_item`` scalar / list / None flattening — branches the
behaviour suites don't exercise.
"""

from __future__ import annotations

from msgspec import Struct

from workspace_app.apps.profiles import list_profiles
from workspace_app.apps.seeding import case_from_item


def test_list_profiles_for_a_missing_app_returns_empty():
    """An App slug with no on-disk ``profiles/`` dir → the iterdir raises
    FileNotFoundError, which `list_profiles` swallows to [] (lines 53-54)."""
    assert list_profiles("no-such-app-slug-xyz") == []


class _Item(Struct):
    """A tiny WorkItem-shaped struct: a scalar, a list, and a None field."""

    title: str
    topics: list[str]
    owner: str | None


def test_case_from_item_flattens_scalars_lists_and_none():
    """`case_from_item` stringifies scalars, joins lists with ', ', and maps a
    None field to '' (line 39)."""
    case = case_from_item(_Item(title="Oven drift", topics=["reflow", "voids"], owner=None))
    assert case["title"] == "Oven drift"  # scalar stringified
    assert case["topics"] == "reflow, voids"  # list joined
    assert case["owner"] == ""  # None → empty string (line 39)
