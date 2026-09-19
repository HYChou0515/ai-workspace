"""The per-scenario workspace has to hold what the skill's body tells the agent
to open. `materialize_skill` gives a real turn the skill's `references/` and
`scripts/` under `.skill/<name>/`; a harness that staged only the scenario's
data files answered every such `read_file` with "no such file", so a body that
says "read `.skill/x/references/r.md` first" could never be measured — the
model would be scored on a step the workspace made impossible.

Where those files come from is ONE rule: the registered skill named by the
body's frontmatter. `--dump-skill` writes `SKILL.md` alone, so the folder an
edited copy sits in says nothing about the skill — its name is whatever `-o`
was, and anything beside it (a previous run's output, say) is not skill
content."""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.skill_eval.__main__ import _resolve_skill, _stage
from workspace_app.skill_eval.scenario import Scenario


def _skill(root: Path, name: str, *, files: dict[str, str], folder: str | None = None) -> Path:
    d = root / (folder or name)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n\nread .skill/{name}/references/r.md"
    )
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return d


def test_staging_puts_the_skills_own_files_where_the_body_points(tmp_path: Path):
    src = _skill(
        tmp_path / "src", "tidy", files={"references/r.md": "the rules", "scripts/x.py": "print(1)"}
    )
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "data.csv").write_text("a,b\n")
    work = tmp_path / "work"

    _stage(Scenario(name="s", prompt="p", data=["data.csv"]), scenarios, work, skill=("tidy", src))

    # The scenario's data at the root, as before…
    assert (work / "data.csv").read_text() == "a,b\n"
    # …and the skill's folder at the path a real workspace holds it, minus
    # SKILL.md (the body reaches the model through the prompt, not the disk).
    assert (work / ".skill" / "tidy" / "references" / "r.md").read_text() == "the rules"
    assert (work / ".skill" / "tidy" / "scripts" / "x.py").read_text() == "print(1)"
    assert not (work / ".skill" / "tidy" / "SKILL.md").exists()


def test_a_skill_with_no_files_of_its_own_stages_nothing_extra(tmp_path: Path):
    src = _skill(tmp_path / "src", "bare", files={})
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    work = tmp_path / "work"

    _stage(Scenario(name="s", prompt="p"), scenarios, work, skill=("bare", src))

    assert not (work / ".skill").exists()


def test_staging_drops_the_same_build_noise_a_real_turn_never_receives(tmp_path: Path):
    """`materialize_skill` copies through `skill_payload`, which leaves
    `__pycache__/`, `*.pyc` and a copy's `.origin` manifest behind. The harness
    stages through the same function, so what the model can `list_files` here
    is what it can list in a real workspace — not a superset."""
    src = _skill(
        tmp_path / "src",
        "tidy",
        files={
            "scripts/x.py": "print(1)",
            "scripts/__pycache__/x.cpython-312.pyc": "junk",
            "scripts/x.pyc": "junk",
            ".origin": "{}",
        },
    )
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    work = tmp_path / "work"

    _stage(Scenario(name="s", prompt="p"), scenarios, work, skill=("tidy", src))

    staged = sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())
    assert staged == [".skill/tidy/scripts/x.py"]


def test_a_registered_name_resolves_to_its_folder(tmp_path: Path, monkeypatch):
    from workspace_app.apps import shared_skills

    src = _skill(tmp_path / "src", "tidy", files={"references/r.md": "rules"})
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "tidy", src)

    name, text, folder = _resolve_skill("tidy")

    assert (name, folder) == ("tidy", src) and "read .skill/tidy/references" in text


def test_an_edited_copy_is_named_by_its_frontmatter_and_files_come_from_the_registry(
    tmp_path: Path, monkeypatch
):
    """The documented loop is `--dump-skill tidy -o ./tune` then
    `--skill ./tune/SKILL.md`: the folder is called `tune`, holds only the
    edited body, and gains a `run-1/` the moment the loop runs once. None of
    that is the skill. The name is the frontmatter's; the files are the
    registered skill's."""
    from workspace_app.apps import shared_skills

    src = _skill(tmp_path / "src", "tidy", files={"references/r.md": "rules"})
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "tidy", src)
    tune = tmp_path / "tune"
    tune.mkdir()
    edited = tune / "SKILL.md"
    edited.write_text((src / "SKILL.md").read_text() + "\n(edited)")
    (tune / "run-1").mkdir()
    (tune / "run-1" / "report.txt").write_text("a previous run")

    name, text, folder = _resolve_skill(str(edited))

    assert name == "tidy"
    assert text.endswith("(edited)")
    assert folder == src

    work = tmp_path / "work"
    _stage(Scenario(name="s", prompt="p"), tmp_path, work, skill=(name, folder))
    staged = sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())
    assert staged == [".skill/tidy/references/r.md"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("---\ndescription: d\n---\n\nbody", "no `name`"),
        ("---\nname: nobody-registered-this\ndescription: d\n---\n\nbody", "unknown skill"),
        ("---\nname: [unbalanced\n---\n\nbody", "malformed"),
    ],
)
def test_a_body_the_harness_cannot_place_is_refused_loudly(tmp_path: Path, text: str, reason: str):
    """A body whose files cannot be found would run — and score the model on
    steps the workspace made impossible, the exact failure staging exists to
    prevent. So it does not run."""
    edited = tmp_path / "SKILL.md"
    edited.write_text(text)

    with pytest.raises(SystemExit) as e:
        _resolve_skill(str(edited))

    assert reason in str(e.value)


def test_an_unregistered_name_is_refused_loudly():
    with pytest.raises(SystemExit) as e:
        _resolve_skill("nobody-registered-this")
    assert "unknown skill" in str(e.value)


def test_the_staged_folder_is_named_by_the_skill_not_by_where_the_registry_keeps_it(
    tmp_path: Path,
):
    """A deployment replaces `SHARED_SKILLS` with its own dict, so the source
    folder can be called anything; the body points at `.skill/<name>/…` where
    `<name>` is the skill's name. The two are the same string only by habit."""
    src = _skill(tmp_path, "tidy", files={"references/r.md": "rules"}, folder="somewhere-else")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    work = tmp_path / "work"

    _stage(Scenario(name="s", prompt="p"), scenarios, work, skill=("tidy", src))

    assert (work / ".skill" / "tidy" / "references" / "r.md").read_text() == "rules"
    assert not (work / ".skill" / "somewhere-else").exists()


def test_the_control_arm_gets_no_skill_files(tmp_path: Path):
    """The control is the run with NO skill loaded. In a real turn the files
    arrive with `read_skill` (`materialize_skill`), so a workspace that never
    loaded the skill never holds them — and a control that could `list_files`
    its way to the reference would pass the very scenario the skill is meant
    to be measured by."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "data.csv").write_text("a,b\n")
    work = tmp_path / "work"

    _stage(Scenario(name="s", prompt="p", data=["data.csv"]), scenarios, work, skill=None)

    assert sorted(p.name for p in work.iterdir()) == ["data.csv"]
