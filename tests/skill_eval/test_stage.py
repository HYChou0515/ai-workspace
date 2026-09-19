"""The per-scenario workspace has to hold what the skill's body tells the agent
to open. `materialize_skill` gives a real turn the skill's `references/` and
`scripts/` under `.skill/<name>/`; a harness that staged only the scenario's
data files answered every such `read_file` with "no such file", so a body that
says "read `.skill/x/references/r.md` first" could never be measured — the
model would be scored on a step the workspace made impossible.

Where those files come from follows the body's frontmatter `name`: the
registered skill of that name when there is one, else the file's own folder
when that folder IS a skill folder — named `<name>`, the invariant the
platform's own loader holds every skill folder to — else the body is refused.
`--dump-skill` writes `SKILL.md` alone into a folder named by `-o`, so an edited
copy's folder says nothing about the skill (and a previous run's output beside
it is not skill content); a profile skill or a work-in-progress folder, whose
name is the skill's, carries its own `references/`."""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.skill_eval.__main__ import _resolve_skill, _stage
from workspace_app.skill_eval.scenario import Scenario


def _skill(root: Path, name: str, *, files: dict[str, str], folder: str | None = None) -> Path:
    """A skill folder at `root/<folder or name>` whose body points at its own
    reference by the full workspace path."""
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
    is what a real workspace holds, minus the `SKILL.md` and `.origin` the
    copy also carries — never more."""
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
    prevent. So it does not run. (`tmp_path` is not named after any skill, so
    the own-folder rule does not apply either.)"""
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


def test_an_unregistered_skill_in_its_own_folder_supplies_its_own_files(tmp_path: Path):
    """A profile skill (`apps/<slug>/profiles/<p>/.skill/<name>/SKILL.md`) or a
    skill still being written is not in `SHARED_SKILLS`, and refusing it would
    make the harness useless for exactly the guidance a deployer writes. Its
    folder IS the skill folder — the platform's loader accepts a folder only
    when it is named by the frontmatter `name` — so that folder is the source."""
    src = _skill(tmp_path / ".skill", "report-format", files={"references/r.md": "rules"})

    name, text, folder = _resolve_skill(str(src / "SKILL.md"))

    assert (name, folder) == ("report-format", src)
    work = tmp_path / "work"
    _stage(Scenario(name="s", prompt="p"), tmp_path, work, skill=(name, folder))
    assert (work / ".skill" / "report-format" / "references" / "r.md").read_text() == "rules"


def test_a_registered_name_wins_over_a_same_named_folder_beside_the_edited_copy(
    tmp_path: Path, monkeypatch
):
    """`--dump-skill tidy -o ./tune/tidy` leaves a folder named like the skill
    holding SKILL.md alone; the registered skill's files are the only ones
    there are, so the registry is asked first."""
    from workspace_app.apps import shared_skills

    src = _skill(tmp_path / "src", "tidy", files={"references/r.md": "rules"})
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "tidy", src)
    dumped = _skill(tmp_path / "tune", "tidy", files={})

    name, _text, folder = _resolve_skill(str(dumped / "SKILL.md"))

    assert (name, folder) == ("tidy", src)


def test_main_gives_the_skill_arm_its_files_and_the_control_arm_none(tmp_path: Path, monkeypatch):
    """`main()` is where the arm decides whether the files are staged; a test
    that calls `_stage(skill=None)` pins the helper, not the line that applies
    it. Drive the real entry with a scripted chat and look at what each arm's
    workspace holds."""
    import json
    import sys

    from workspace_app.apps import shared_skills
    from workspace_app.skill_eval import __main__ as cli
    from workspace_app.skill_eval.runner import Turn

    src = _skill(tmp_path / "src", "tidy", files={"references/r.md": "rules"})
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "tidy", src)
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "s.json").write_text(json.dumps({"name": "s", "prompt": "p"}))
    out = tmp_path / "out"

    class Cfg:
        model = "scripted"
        system_prompt = "sys"

    monkeypatch.setattr(cli, "_resolve_agent", lambda *a, **k: Cfg())
    monkeypatch.setattr(cli, "_litellm_chat", lambda cfg, n, t: lambda m, tools: Turn("done"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "skill_eval",
            "--skill",
            "tidy",
            "--scenarios",
            str(scenarios),
            "--control",
            "-o",
            str(out),
        ],
    )

    with pytest.raises(SystemExit) as e:
        cli.main()

    assert e.value.code == 0  # a scenario with no expectations passes both arms
    assert (out / "s.skill" / ".skill" / "tidy" / "references" / "r.md").read_text() == "rules"
    assert not (out / "s.control" / ".skill").exists()
