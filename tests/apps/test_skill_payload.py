"""#589 — a baked-in skill is a FOLDER, not a lone markdown file.

Until now every skill reader hardcoded `SKILL.md`, so a `scripts/` or
`references/` committed next to it was a complete no-op: no error, no log, no
validation. These tests pin the payload that a shared / profile skill actually
ships, which is the input to materializing it into a workspace.
"""

from pathlib import Path

from workspace_app.apps.skill_payload import origin_for, skill_payload


def _skill(tmp_path: Path) -> Path:
    src = tmp_path / "triage-reflow"
    (src / "references").mkdir(parents=True)
    (src / "scripts").mkdir()
    (src / "SKILL.md").write_bytes(b"---\nname: triage-reflow\n---\nbody")
    (src / "references" / "glossary.md").write_bytes(b"# terms")
    (src / "scripts" / "summarise.py").write_bytes(b"print('hi')\n")
    return src


def test_payload_carries_the_whole_folder_keyed_by_relative_path(tmp_path: Path):
    assert skill_payload(_skill(tmp_path)) == {
        "SKILL.md": b"---\nname: triage-reflow\n---\nbody",
        "references/glossary.md": b"# terms",
        "scripts/summarise.py": b"print('hi')\n",
    }


# Build noise is not skill content. The exclusion list is deliberately TINY:
# `seeding._SKIP` also drops `run.py` / `__init__.py`, but that list describes
# PROFILE-level noise (a workflow's host-side orchestration, package markers).
# Inside a skill folder those are perfectly ordinary scripts, and dropping one
# would recreate the exact silent-loss bug this issue is about.
def test_drops_build_noise_but_keeps_ordinary_python_files(tmp_path: Path):
    src = _skill(tmp_path)
    (src / "scripts" / "__pycache__").mkdir()
    (src / "scripts" / "__pycache__" / "summarise.cpython-312.pyc").write_bytes(b"\x00compiled")
    (src / "scripts" / "stale.pyc").write_bytes(b"\x00compiled")
    (src / "scripts" / "run.py").write_bytes(b"# the skill's own entry point\n")
    (src / "scripts" / "__init__.py").write_bytes(b"")

    payload = skill_payload(src)

    assert "scripts/run.py" in payload
    assert "scripts/__init__.py" in payload
    assert not [p for p in payload if "__pycache__" in p or p.endswith(".pyc")]


# The manifest we write next to a materialized copy is bookkeeping, not content.
# A source folder should never contain one, but a materialized copy DOES — and a
# copy can become a source (download a skill, hand it to a dev, commit it under
# sample-skills/). Carrying it would nest one generation's bookkeeping inside the
# next and make "was this file modified locally?" answer about the wrong thing.
def test_never_carries_the_origin_manifest(tmp_path: Path):
    src = _skill(tmp_path)
    (src / ".origin").write_bytes(b'{"source":"shared","files":{}}')

    assert ".origin" not in skill_payload(src)


# The manifest answers exactly two questions later: "is a newer version shipped?"
# (compare the whole map against the current source) and "did the AI edit THIS
# file?" (compare one entry against the workspace copy). Both are per-file, so
# the manifest is per-file — there is no separate whole-folder digest to disagree
# with it.
def test_origin_records_one_hash_per_shipped_file(tmp_path: Path):
    origin = origin_for("shared", skill_payload(_skill(tmp_path)))

    assert origin.source == "shared"
    assert sorted(origin.files) == ["SKILL.md", "references/glossary.md", "scripts/summarise.py"]


def test_editing_a_file_changes_only_that_file_s_hash(tmp_path: Path):
    src = _skill(tmp_path)
    before = origin_for("profile", skill_payload(src))

    (src / "scripts" / "summarise.py").write_bytes(b"print('improved')\n")
    after = origin_for("profile", skill_payload(src))

    assert after.files["scripts/summarise.py"] != before.files["scripts/summarise.py"]
    assert after.files["SKILL.md"] == before.files["SKILL.md"]
