"""The per-scenario workspace has to hold what the skill's body tells the agent
to open. `materialize_skill` gives a real turn the skill's `references/` and
`scripts/` under `.skill/<name>/`; a harness that staged only the scenario's
data files answered every such `read_file` with "no such file", so a body that
says "read `references/x.md` first" could never be measured — the model would
be scored on a step the workspace made impossible."""

from __future__ import annotations

from pathlib import Path

from workspace_app.skill_eval.__main__ import _resolve_skill, _stage
from workspace_app.skill_eval.scenario import Scenario


def _skill(root: Path, name: str, *, files: dict[str, str]) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nread references/r.md")
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

    _stage(Scenario(name="s", prompt="p", data=["data.csv"]), scenarios, work, skill_dir=src)

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

    _stage(Scenario(name="s", prompt="p"), scenarios, work, skill_dir=src)

    assert not (work / ".skill").exists()


def test_resolving_a_registered_skill_or_an_edited_copy_names_its_folder(
    tmp_path: Path, monkeypatch
):
    """`--dump-skill` writes SKILL.md alone; an edited copy passed back by path
    still has to find its `references/` — the harness looks beside the edited
    file first, and falls back to the registered folder of the same name."""
    from workspace_app.apps import shared_skills

    src = _skill(tmp_path / "src", "tidy", files={"references/r.md": "rules"})
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "tidy", src)

    name, text, folder = _resolve_skill("tidy")
    assert (name, folder) == ("tidy", src) and "read references" in text

    # An edited copy in a folder of the same name, WITHOUT the references beside
    # it: the registered folder supplies them.
    edited = tmp_path / "tune" / "tidy" / "SKILL.md"
    edited.parent.mkdir(parents=True)
    edited.write_text(text + "\n(edited)")
    name, text2, folder2 = _resolve_skill(str(edited))
    assert name == "tidy" and text2.endswith("(edited)") and folder2 == src
