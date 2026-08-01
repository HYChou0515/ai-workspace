"""The skill we hand someone who wants to use a tool here (#674).

The entry point is the tool's own GitLab repository — that is the one thing
the person already has. So the path is: install this skill once, give it a
repo URL, and let the agent do the assembly.

Which means the skill has to cover more than the happy path. Whoever is
following it is alone with a failure, and the three parties who can fix
things — the tool's author, the platform team, and the person themselves —
are different people.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SKILL = _REPO / "tool-skill" / "SKILL.md"


@pytest.fixture
def skill() -> str:
    return _SKILL.read_text("utf-8")


def test_the_skill_announces_itself_well_enough_to_be_chosen(skill: str) -> None:
    """An agent picks a skill from its description alone. One that only says
    what it is, and not when to reach for it, is never reached for."""
    head = skill.split("---")[1]

    assert "name: install-workspace-tool" in head
    assert "description:" in head
    # The two moments someone needs it, in the words they would use.
    assert "GitLab" in head
    assert "not available" in head


def test_an_unfilled_copy_refuses_to_guess(skill: str) -> None:
    """The runner image is the one fact the skill cannot derive, so the
    platform team fills it in before handing the skill out. A copy that went
    out unfilled must stop and say so — an agent that helpfully found "some
    MCP image" on a registry would be running a stranger's code."""
    assert "<<RUNNER_IMAGE>>" in skill
    assert "stop" in skill.lower()
    assert "do not guess" in skill.lower()


def test_the_ambiguous_failure_is_spelled_out(skill: str) -> None:
    """404 means either "the artifact expired" or "you may not see this
    project", and GitLab gives no way to tell them apart. They are fixed by
    different people, so a skill that treated 404 as one thing would send
    half of its readers to the wrong one.

    The test in the skill — open the URL in a browser — is the only one that
    separates them."""
    assert "404" in skill
    assert "expire_in: never" in skill
    assert "browser" in skill.lower()


def test_every_failure_names_who_fixes_it(skill: str) -> None:
    # "It failed" is not actionable. "Ask the author to rebuild" is.
    for party in ("tool's author", "platform team", "The person"):
        assert party in skill, party


def test_the_config_the_skill_writes_carries_nothing_machine_specific(skill: str) -> None:
    """The whole reason the runner drops privileges itself. A `--user` in the
    skill's example would be copied into every config it ever writes, and be
    wrong on the second machine."""
    assert "--user" in skill  # it is mentioned…
    assert "Do not add `--user`" in skill  # …only to forbid it


def test_the_skill_refuses_the_two_shortcuts_that_look_helpful(skill: str) -> None:
    """Both are things a capable agent would otherwise try, and both defeat a
    gate that exists for a reason: pinning an old job id to dodge an expired
    artifact freezes someone on a version nobody else has, and swapping the
    runner image runs a bundle against a base its compiled dependencies were
    not built for."""
    assert "older job id" in skill
    assert "Do not substitute a different image" in skill


def test_the_skill_makes_the_installer_prove_it_works(skill: str) -> None:
    # Writing a config file is not installing. The first run also downloads
    # the bundle, which is where most failures actually surface.
    assert "tools/list" in skill
    assert "without step 3" in skill


# ─── anti-drift ─────────────────────────────────────────────────────
#
# The other half lives in `sandbox-host/tests/test_mcp_runner.py`, which can
# RUN the runner and compare what it actually prints against this file.
# Matching source text here would have compared against a string the code
# assembles at runtime, and passed while the two drifted.


def test_the_skill_covers_the_refusals_the_platform_can_produce(skill: str) -> None:
    """Each of these is a distinct gate with a distinct owner: the ABI anchor
    (author rebuilds), the integrity check (author republishes), the size
    limit and its certificate (author asks us). A refusal the skill cannot
    explain arrives as a wall of text nobody can act on."""
    for gate in ("builder", "sha256", "certificate", "limit"):
        assert gate in skill, gate
