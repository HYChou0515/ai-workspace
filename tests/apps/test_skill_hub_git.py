"""The skill hub's git storage (docs/plan-skill-hub-history.md §3.1, §4.7).

The pure half — blob ids, LFS pointers, which paths go to LFS — is checked
against git itself (`git hash-object`), so "our formula equals git's" is a
parity test with git as the oracle, not two implementations kept alike by hand.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from workspace_app.apps.skill_hub_git import (
    GITATTRIBUTES,
    SkillHubRepos,
    git_blob_id,
    is_lfs_path,
    lfs_pointer,
    parse_lfs_pointer,
    same_content,
)


@pytest.mark.parametrize("data", [b"", b"hello\n", "日本語\n".encode(), bytes(range(256)) * 5])
def test_the_blob_id_is_the_one_git_computes(data: bytes) -> None:
    oracle = (
        subprocess.run(
            ["git", "hash-object", "--stdin"], input=data, capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    assert git_blob_id(data) == oracle


@pytest.mark.parametrize(
    ("path", "lfs"),
    [
        ("diagram.png", True),
        ("references/shot.JPG", False),  # .gitattributes patterns are case-sensitive, like git's
        ("references/deep/report.pdf", True),
        ("data/archive.tar", True),
        ("SKILL.md", False),
        ("references/notes.txt", False),
        ("scripts/run.py", False),
        ("references/table.csv", False),
    ],
)
def test_lfs_paths_are_the_fixed_patterns_at_any_depth(path: str, lfs: bool) -> None:
    assert is_lfs_path(path) is lfs


def test_the_attributes_file_names_every_pattern_the_rule_uses() -> None:
    """`is_lfs_path` and `.gitattributes` must be one list: a pattern only in
    the rule writes pointers git does not know are pointers."""
    from workspace_app.apps.skill_hub_git import LFS_PATTERNS

    lines = [ln for ln in GITATTRIBUTES.decode().splitlines() if ln.strip()]
    assert [ln.split()[0] for ln in lines] == list(LFS_PATTERNS)
    assert all(ln.endswith("filter=lfs diff=lfs merge=lfs -text") for ln in lines)


def test_a_pointer_round_trips_and_names_the_content() -> None:
    data = b"\x89PNG" + b"x" * 5000
    pointer = lfs_pointer(data)
    assert pointer.startswith(b"version https://git-lfs.github.com/spec/v1\n")
    assert parse_lfs_pointer(pointer) == (hashlib.sha256(data).hexdigest(), len(data))


@pytest.mark.parametrize(
    "blob",
    [b"", b"version https://git-lfs.github.com/spec/v1\n", b"# a skill\n", b"oid sha256:abc\n"],
)
def test_anything_else_is_not_a_pointer(blob: bytes) -> None:
    assert parse_lfs_pointer(blob) is None


# ── the repos ────────────────────────────────────────────────────────────────


_PNG = b"\x89PNG\r\n" + bytes(range(256)) * 40
_PAYLOAD = {
    "SKILL.md": b"---\nname: s\ndescription: d\n---\nbody\n",
    "references/notes.md": b"notes\n",
    "references/shot.png": _PNG,
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True
    ).stdout.decode()


async def test_a_version_reads_back_as_the_files_it_was_written_from(tmp_path: Path) -> None:
    repos = SkillHubRepos(tmp_path)
    commit = await repos.write_version("e1", _PAYLOAD, parent=None, author="alice", message="v1")

    assert await repos.read("e1", commit) == _PAYLOAD
    # The tree is a standard LFS repo: the image is a pointer, its bytes are
    # under lfs/objects, and the attributes file says so.
    repo = repos.path("e1")
    pointer = _git(repo, "show", f"{commit}:references/shot.png").encode()
    assert parse_lfs_pointer(pointer) == (hashlib.sha256(_PNG).hexdigest(), len(_PNG))
    oid = hashlib.sha256(_PNG).hexdigest()
    assert (repo / "lfs" / "objects" / oid[:2] / oid[2:4] / oid).read_bytes() == _PNG
    assert _git(repo, "show", f"{commit}:.gitattributes").encode() == GITATTRIBUTES
    assert _git(repo, "log", "-1", "--format=%an", commit).strip() == "alice"


async def test_writing_a_version_does_not_move_master(tmp_path: Path) -> None:
    repos = SkillHubRepos(tmp_path)
    await repos.write_version("e1", _PAYLOAD, parent=None, author="alice", message="v1")
    assert await repos.master("e1") is None


async def test_master_moves_only_from_the_value_the_mover_read(tmp_path: Path) -> None:
    """G5: the lease IS the lock — whoever moved master first wins, the other is told."""
    repos = SkillHubRepos(tmp_path)
    c1 = await repos.write_version("e1", _PAYLOAD, parent=None, author="a", message="1")
    c2 = await repos.write_version(
        "e1", {**_PAYLOAD, "SKILL.md": b"v2"}, parent=c1, author="a", message="2"
    )

    assert await repos.move_master("e1", c1, expected=None) is True
    assert await repos.move_master("e1", c2, expected=None) is False  # someone created it first
    assert await repos.move_master("e1", c2, expected=c2) is False  # stale read
    assert await repos.move_master("e1", c2, expected=c1) is True
    assert await repos.move_master("e1", c1, expected=c2) is True  # a rollback rewinds
    assert await repos.master("e1") == c1


async def test_a_version_on_top_of_another_names_it_as_parent(tmp_path: Path) -> None:
    repos = SkillHubRepos(tmp_path)
    c1 = await repos.write_version("e1", _PAYLOAD, parent=None, author="a", message="1")
    c2 = await repos.write_version("e1", {"SKILL.md": b"v2"}, parent=c1, author="a", message="2")
    assert _git(repos.path("e1"), "rev-parse", f"{c2}^").strip() == c1
    # Replace, not merge: what v2 dropped is gone from v2.
    assert await repos.read("e1", c2) == {"SKILL.md": b"v2"}


async def test_every_revision_tag_names_its_commit_and_back(tmp_path: Path) -> None:
    """G7: a revision id carries `:` (`<id>:N`), which a git ref cannot."""
    repos = SkillHubRepos(tmp_path)
    c1 = await repos.write_version("e1", _PAYLOAD, parent=None, author="a", message="1")
    await repos.tag("e1", "e1:1", c1)
    await repos.tag("e1", "e1:2", c1)
    assert await repos.tagged_revisions("e1") == {"e1:1": c1, "e1:2": c1}


async def test_the_tree_answers_what_each_file_is_without_its_bytes(tmp_path: Path) -> None:
    repos = SkillHubRepos(tmp_path)
    commit = await repos.write_version("e1", _PAYLOAD, parent=None, author="a", message="1")
    tree = await repos.tree("e1", commit)

    assert sorted(tree) == sorted(_PAYLOAD)  # `.gitattributes` is the platform's, not the skill's
    for rel, data in _PAYLOAD.items():
        assert same_content(tree[rel], data), rel
        assert not same_content(tree[rel], data + b"!"), rel


async def test_reading_some_paths_reads_only_those(tmp_path: Path) -> None:
    repos = SkillHubRepos(tmp_path)
    commit = await repos.write_version("e1", _PAYLOAD, parent=None, author="a", message="1")
    got = await repos.read("e1", commit, paths=["references/shot.png"])
    assert got == {"references/shot.png": _PNG}


# ── where the repos live (G2) ────────────────────────────────────────────────


def test_the_setting_is_read_from_config(tmp_path: Path) -> None:
    from workspace_app.config.loader import load

    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"skill_hub:\n  git_root: {tmp_path / 'hub'}\n")
    assert load(config_path=cfg, env={}).skill_hub.git_root == str(tmp_path / "hub")
    assert load(config_path=None, env={}).skill_hub.git_root == ""


def test_a_set_root_is_used_as_is(tmp_path: Path) -> None:
    from workspace_app.apps.skill_hub_git import resolve_git_root

    assert resolve_git_root(str(tmp_path / "hub"), durable=True) == tmp_path / "hub"


def test_an_unset_root_is_a_throwaway_dir_only_where_nothing_else_is_durable() -> None:
    from workspace_app.apps.skill_hub_git import resolve_git_root

    scratch = resolve_git_root("", durable=False)
    assert scratch.is_dir()


def test_an_unset_root_refuses_to_boot_a_durable_deploy() -> None:
    """A deploy whose rows survive a restart cannot keep their versions in a
    dir that does not: every `commit` would name a repo that is gone."""
    from workspace_app.apps.skill_hub_git import resolve_git_root

    with pytest.raises(ValueError, match="skill_hub.git_root"):
        resolve_git_root("", durable=True)


# ── a file that only LOOKS like a pointer (review round 1, defect #1 / #8) ────


def _forged(oid: str) -> bytes:
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 11\n".encode()


@pytest.mark.parametrize("path", ["notes.txt", "references/lfs-howto.md"])
async def test_a_text_file_shaped_like_a_pointer_is_read_as_the_text_it_is(
    tmp_path: Path, path: str
) -> None:
    """Only a path the platform stores in LFS holds a pointer — the platform
    wrote it. Anywhere else the bytes are the user's, read back as written,
    never followed: following them read any file the API pod could see."""
    secret = tmp_path / "secret"
    secret.write_text("HOST-SECRET")
    repos = SkillHubRepos(tmp_path / "git")
    payload = {"SKILL.md": b"x", path: _forged(f"../.{secret}")}
    commit = await repos.write_version("e1", payload, parent=None, author="a", message="1")

    assert (await repos.tree("e1", commit))[path].lfs is None
    assert await repos.read("e1", commit) == payload


def test_a_pointer_names_its_content_by_a_sha256_and_nothing_else() -> None:
    assert parse_lfs_pointer(_forged("../../etc/passwd")) is None
    assert parse_lfs_pointer(_forged("A" * 64)) is None
    assert parse_lfs_pointer(_forged("a" * 64)) == ("a" * 64, 11)


async def test_a_well_formed_pointer_in_a_text_path_is_still_just_text(tmp_path: Path) -> None:
    """The oid check alone is not the guard: a valid sha256 naming another
    file's LFS object is a well-formed pointer, and a text path holding one
    must read back as the text, not as that other file."""
    repos = SkillHubRepos(tmp_path / "git")
    real = hashlib.sha256(_PNG).hexdigest()
    payload = {"SKILL.md": b"x", "shot.png": _PNG, "notes.txt": _forged(real)}
    commit = await repos.write_version("e1", payload, parent=None, author="a", message="1")

    assert await repos.read("e1", commit) == payload
