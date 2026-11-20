"""Packing a folder: which files make up a job, and what makes two jobs the same."""

from __future__ import annotations

import hashlib
import os

import pytest

from job_tool.pack import SKIPPED_DIRS, job_hash, pack


def _write(root, rel, data=b"x"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_every_file_is_listed_by_its_path_in_the_folder_with_size_and_hash(tmp_path):
    _write(tmp_path, "run.sh", b"python train.py\n")
    _write(tmp_path, "src/train.py", b"print(1)\n")
    _write(tmp_path, "data/empty.csv", b"")

    packed = pack(tmp_path)

    assert [(f.path, f.size) for f in packed.files] == [
        ("data/empty.csv", 0),
        ("run.sh", 16),
        ("src/train.py", 9),
    ]
    assert packed.files[1].sha256 == hashlib.sha256(b"python train.py\n").hexdigest()
    assert packed.total == 25


@pytest.mark.parametrize("name", sorted(SKIPPED_DIRS))
def test_environments_caches_and_version_control_are_not_sent(tmp_path, name):
    # They are large, rebuilt on the other side, or both; and they are what
    # makes a folder of a few KB weigh gigabytes.
    _write(tmp_path, "run.sh")
    _write(tmp_path, f"{name}/lib/thing.py")
    _write(tmp_path, f"src/{name}/deep.py")

    assert [f.path for f in pack(tmp_path).files] == ["run.sh"]


def test_compiled_python_is_not_sent(tmp_path):
    _write(tmp_path, "run.sh")
    _write(tmp_path, "train.pyc")

    assert [f.path for f in pack(tmp_path).files] == ["run.sh"]


def test_what_the_folders_gitignore_names_is_not_sent(tmp_path):
    _write(tmp_path, ".gitignore", b"checkpoints/\n*.ckpt\n!keep.ckpt\n")
    _write(tmp_path, "run.sh")
    _write(tmp_path, "checkpoints/epoch1.pt")
    _write(tmp_path, "old.ckpt")
    _write(tmp_path, "keep.ckpt")

    assert [f.path for f in pack(tmp_path).files] == [".gitignore", "keep.ckpt", "run.sh"]


def test_links_are_not_followed_and_are_reported(tmp_path):
    # A link can lead anywhere in the sandbox; sending what it points at would
    # send what the person never put in the folder.
    outside = tmp_path / "outside"
    _write(outside, "secret.txt")
    folder = tmp_path / "job"
    _write(folder, "run.sh")
    os.symlink(outside / "secret.txt", folder / "link.txt")
    os.symlink(outside, folder / "linkdir")

    packed = pack(folder)

    assert [f.path for f in packed.files] == ["run.sh"]
    assert packed.skipped == ("link.txt", "linkdir")


def test_the_records_folder_is_never_sent_even_when_the_folder_is_the_workspace(tmp_path):
    _write(tmp_path, "run.sh")
    _write(tmp_path, "jobs/j1/job.json")

    packed = pack(tmp_path, never=tmp_path / "jobs")

    assert [f.path for f in packed.files] == ["run.sh"]


def test_a_jobs_folder_that_is_not_the_records_folder_is_sent(tmp_path):
    _write(tmp_path, "train/run.sh")
    _write(tmp_path, "train/jobs/list.txt")

    packed = pack(tmp_path / "train", never=tmp_path / "jobs")

    assert [f.path for f in packed.files] == ["jobs/list.txt", "run.sh"]


# ── sameness ─────────────────────────────────────────────────────────────────


def _hash(root, resources=None, item="item-1"):
    return job_hash(pack(root), {"gpus": 1} if resources is None else resources, item)


def test_the_same_content_is_the_same_job_whenever_it_was_written(tmp_path):
    _write(tmp_path, "run.sh", b"a")
    first = _hash(tmp_path)
    os.utime(tmp_path / "run.sh", (1, 1))

    assert _hash(tmp_path) == first


@pytest.mark.parametrize(
    "change",
    ["a byte", "a name", "a new file", "the resources", "the item"],
)
def test_anything_the_job_is_made_of_changes_its_hash(tmp_path, change):
    _write(tmp_path, "run.sh", b"a")
    before = _hash(tmp_path)

    resources, item = None, "item-1"
    if change == "a byte":
        _write(tmp_path, "run.sh", b"b")
    elif change == "a name":
        (tmp_path / "run.sh").rename(tmp_path / "go.sh")
    elif change == "a new file":
        _write(tmp_path, "data.csv", b"")
    elif change == "the resources":
        resources = {"gpus": 2}
    else:
        item = "item-2"

    assert _hash(tmp_path, resources, item) != before


def test_the_order_resources_are_written_in_does_not_matter(tmp_path):
    _write(tmp_path, "run.sh")

    assert _hash(tmp_path, {"gpus": 1, "hours": 2}) == _hash(tmp_path, {"hours": 2, "gpus": 1})


def test_no_item_is_not_any_items_hash(tmp_path):
    # The MCP runner on an engineer's machine has no item (D47 of the old plan).
    _write(tmp_path, "run.sh")

    assert _hash(tmp_path, item=None) != _hash(tmp_path, item="")
