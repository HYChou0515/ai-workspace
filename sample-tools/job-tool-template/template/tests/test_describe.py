"""What the model reads about each command: `run` is the backend's words
followed by the tool's; the others are the tool's unless the backend says
otherwise (D4)."""

from __future__ import annotations

import pytest
from backends import Remote, RemoteWithQueue

from job_tool.describe import describe


def test_run_starts_with_the_backends_own_words():
    text = describe("run", Remote)

    assert text.startswith(Remote.run_description)


@pytest.mark.parametrize(
    "said",
    [
        ".gitignore",  # what is not sent
        "links",
        "call run again",  # a large folder takes several calls
        "same folder",  # sameness
        "until it finishes",
        "nothing announces",  # D2
        "jobs",
        "small",  # try it here first
    ],
)
def test_run_says_how_the_tool_behaves(said):
    assert said in describe("run", Remote)


def test_run_points_at_queue_only_when_there_is_one():
    assert "queue" not in describe("run", Remote).replace(Remote.run_description, "")
    assert "queue" in describe("run", RemoteWithQueue)


@pytest.mark.parametrize("name", ["jobs", "queue", "files", "fetch", "logs", "cancel"])
def test_every_other_command_has_a_description_of_its_own(name):
    assert describe(name, RemoteWithQueue).strip()


def test_jobs_says_unknown_is_not_gone():
    text = describe("jobs", Remote)
    assert "unknown" in text and "not" in text


def test_a_backends_own_description_replaces_the_templates():
    class Worded(Remote):
        descriptions = {"logs": "The scheduler's stdout and stderr, merged."}

    assert describe("logs", Worded) == "The scheduler's stdout and stderr, merged."
    assert describe("jobs", Worded) == describe("jobs", Remote)
