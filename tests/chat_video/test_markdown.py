"""`markdown.abs_path`: the ONE spelling of a workspace path, shared by the
timeline (which files to read), the page's asset table and the `<img>`
lookups — so `plots/a.png`, `./plots/a.png` and `plots//a.png` are one key.

A `..` that climbs above the workspace root is the exception and is kept as
written: the chat's own URL for it resolves above `/files/` and draws
nothing, and the CLI's jail refuses it — folding it back inside would draw
a picture the chat did not (round 4).
"""

from __future__ import annotations

import pytest

from workspace_app.chat_video.markdown import abs_path

SPELLINGS = {
    "relative": ("plots/a.png", "/plots/a.png"),
    "absolute": ("/plots/a.png", "/plots/a.png"),
    "dot-slash": ("./plots/a.png", "/plots/a.png"),
    "double slash": ("plots//a.png", "/plots/a.png"),
    "leading double slash": ("//plots/a.png", "/plots/a.png"),
    "dot inside": ("/plots/./a.png", "/plots/a.png"),
    "dot-dot inside": ("plots/../a.png", "/a.png"),
    "trailing slash": ("plots/", "/plots"),
    "bare dot": (".", "/"),
    "empty": ("", "/"),
    "backslash is a name": ("plots\\a.png", "/plots\\a.png"),
    # Climbing above the root: kept as written, never folded back inside.
    "dot-dot escaping": ("../a.png", "/../a.png"),
    "absolute dot-dot escaping": ("/../a.png", "/../a.png"),
    "escaping after a descent": ("a/../../b.png", "/a/../../b.png"),
    "bare dot-dot": ("..", "/.."),
    # Escaping, then descending into a name a detector's sentinel could
    # collide with (`/../w` normalises to `/w`): still an escape.
    "escaping then re-entering": ("../w", "/../w"),
    "escaping then re-entering deeper": ("../w/a.png", "/../w/a.png"),
}


@pytest.mark.parametrize("case", SPELLINGS)
def test_every_spelling_of_a_path_has_one_key(case: str):
    raw, key = SPELLINGS[case]

    assert abs_path(raw) == key
