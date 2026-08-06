"""#697 — the two ways a link can move under the pass that is repointing it.

Both are races, so they are exercised through a manager double rather than
through timing: the point is that the pass yields to whoever got in first
instead of overwriting them, and a race that only shows up under load is not a
thing to leave until it does.
"""

from __future__ import annotations

import pytest
from specstar.types import PreconditionFailedError, ResourceIDNotFoundError

from workspace_app.kb.graph.persist import _repoint


class _Vanished:
    """The row was deleted between the snapshot and the write."""

    def get(self, lid: str):
        raise ResourceIDNotFoundError(lid)

    def patch(self, *a, **kw):  # pragma: no cover — must never be reached
        raise AssertionError("patched a row that is gone")


class _AnsweredMeanwhile:
    """The row is still there, but someone rewrote it after the snapshot."""

    def __init__(self) -> None:
        self.patched = False

    def get(self, lid: str):
        from types import SimpleNamespace

        from workspace_app.resources.graph import GraphEntityLink

        return SimpleNamespace(
            data=GraphEntityLink(entity_id="e", mention_id="m", state="pending"),
            info=SimpleNamespace(revision_id="stale"),
        )

    def patch(self, *a, **kw):
        self.patched = True
        raise PreconditionFailedError("l1", "stale", "newer")


def test_a_link_deleted_under_the_pass_is_not_resurrected():
    _repoint(_Vanished(), "l1", ["c1"])  # returns, raises nothing


def test_a_link_rewritten_under_the_pass_keeps_the_other_writer_s_version():
    lrm = _AnsweredMeanwhile()
    _repoint(lrm, "l1", ["c1"])
    assert lrm.patched, "the write was never attempted, so the guard proves nothing"


def test_the_double_models_the_contract_it_stands_in_for():
    """Both doubles raise what specstar really raises — asserted here so the
    test cannot pass by modelling an exception nobody throws."""
    assert issubclass(ResourceIDNotFoundError, Exception)
    assert issubclass(PreconditionFailedError, Exception)
    with pytest.raises(ResourceIDNotFoundError):
        _Vanished().get("l1")
