"""Skeleton placeholder substitution (#419 §D) — the closed vocabulary and its
one escape (an unknown token is left verbatim, never evaluated)."""

from __future__ import annotations

from workspace_app.entity.skeleton import render_skeleton


def test_render_substitutes_every_placeholder_kind() -> None:
    template = (
        "n={{number}} at={{now}} by={{actor}} t={{arg.title}} o=[{{arg.opt?}}] raw={{unknown}}"
    )

    out = render_skeleton(template, {"title": "X"}, number=7, now="2026-07-03", actor="alice")

    assert out == "n=7 at=2026-07-03 by=alice t=X o=[] raw={{unknown}}"
