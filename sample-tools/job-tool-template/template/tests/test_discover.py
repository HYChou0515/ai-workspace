"""Finding the backend: exactly one, or the tool refuses to start and says why."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest
from backends import Remote

from job_tool.discover import GROUP, NoBackend, load_backend


def _ep(name: str, value: str) -> EntryPoint:
    return EntryPoint(name=name, value=value, group=GROUP)


def test_the_one_registered_backend_is_the_backend():
    assert load_backend([_ep("acme", "backends:Remote")]) is Remote


def test_no_backend_is_refused_naming_where_to_register_one():
    with pytest.raises(NoBackend, match=GROUP):
        load_backend([])


def test_two_backends_are_refused_naming_both():
    # Picking one would make "which backend did that job go to?" unanswerable.
    with pytest.raises(NoBackend, match="acme.*other"):
        load_backend([_ep("acme", "backends:Remote"), _ep("other", "backends:RemoteWithQueue")])


def test_a_registered_backend_that_cannot_keep_its_contract_is_refused():
    # This is the gate CI runs (the shape tests load the backend): a missing
    # call fails here instead of shipping a command that fails every time.
    with pytest.raises(NoBackend, match="status"):
        load_backend([_ep("acme", "backends:Missing")])


def test_a_resources_schema_the_validator_rejects_is_refused():
    # It would reject every run made with it.
    with pytest.raises(NoBackend, match="resources_schema"):
        load_backend([_ep("acme", "backends:BadSchema")])


def test_something_registered_that_is_not_a_backend_is_refused():
    with pytest.raises(NoBackend, match="backends:RESOURCES"):
        load_backend([_ep("acme", "backends:RESOURCES")])
