"""#397 request_wiki_update — the agent tool that submits a user's wiki
correction to the maintainer queue (both KB chat + app agents get it). It
resolves the target collection from the turn's in-scope wiki-enabled collections
and delegates to the ctx-bound submit callback (WikiMaintenanceCoordinator)."""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, build_tools
from workspace_app.agent.tools import request_wiki_update_impl
from workspace_app.kb.wiki.corrections import WikiNotEnabledError
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection


def _wiki_collection(spec, name: str) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, use_wiki=True))
        .resource_id
    )


class _RecordingSubmit:
    """Stands in for WikiMaintenanceCoordinator.submit_correction."""

    def __init__(self, path: str = "/corrections/x.md") -> None:
        self.calls: list[dict] = []
        self._path = path

    async def __call__(self, collection_id, **kw):
        self.calls.append({"collection_id": collection_id, **kw})
        return self._path


async def test_submits_the_correction_for_the_single_wiki_collection_in_scope():
    spec = make_spec(default_user="u")
    cid = _wiki_collection(spec, "Defects")
    submit = _RecordingSubmit("/corrections/entities-foo.md")
    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec, collection_ids=[cid], acting_user="alice", submit_wiki_correction=submit
        )
    )
    out = await request_wiki_update_impl(
        ctx,
        instruction="Foo was founded in 1998, not 1989.",
        target_page="/entities/foo.md",
        reference="Annual report p.3.",
    )
    assert len(submit.calls) == 1
    call = submit.calls[0]
    assert call["collection_id"] == cid
    assert call["instruction"] == "Foo was founded in 1998, not 1989."
    assert call["target_page"] == "/entities/foo.md"
    assert call["reference"] == "Annual report p.3."
    assert call["requested_by"] == "alice"
    assert "error" not in out.lower()  # a success confirmation


async def test_requires_a_collection_when_more_than_one_wiki_is_in_scope():
    spec = make_spec(default_user="u")
    c1 = _wiki_collection(spec, "A")
    c2 = _wiki_collection(spec, "B")
    submit = _RecordingSubmit()
    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec, collection_ids=[c1, c2], acting_user="a", submit_wiki_correction=submit
        )
    )
    out = await request_wiki_update_impl(ctx, instruction="something is wrong")
    assert out.startswith("error")
    assert "collection" in out.lower()  # asks the agent to name one
    assert submit.calls == []  # nothing submitted


async def test_names_the_collection_explicitly():
    spec = make_spec(default_user="u")
    c1 = _wiki_collection(spec, "A")
    c2 = _wiki_collection(spec, "B")
    submit = _RecordingSubmit()
    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec, collection_ids=[c1, c2], acting_user="a", submit_wiki_correction=submit
        )
    )
    out = await request_wiki_update_impl(ctx, instruction="fix", collection="B")
    assert "error" not in out.lower()
    assert submit.calls[0]["collection_id"] == c2


async def test_errors_when_no_wiki_collection_is_in_scope():
    spec = make_spec(default_user="u")
    plain = spec.get_resource_manager(Collection).create(Collection(name="plain")).resource_id
    submit = _RecordingSubmit()
    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec, collection_ids=[plain], acting_user="a", submit_wiki_correction=submit
        )
    )
    out = await request_wiki_update_impl(ctx, instruction="fix")
    assert out.startswith("error")
    assert submit.calls == []


async def test_unavailable_when_the_turn_has_no_submit_binding():
    spec = make_spec(default_user="u")
    cid = _wiki_collection(spec, "Defects")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))  # no callback
    out = await request_wiki_update_impl(ctx, instruction="fix")
    assert out.startswith("error")


async def test_maps_wiki_not_enabled_to_a_friendly_error():
    spec = make_spec(default_user="u")
    cid = _wiki_collection(spec, "Defects")

    async def _raises(collection_id, **kw):
        raise WikiNotEnabledError(collection_id)

    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec, collection_ids=[cid], acting_user="a", submit_wiki_correction=_raises
        )
    )
    out = await request_wiki_update_impl(ctx, instruction="fix")
    assert out.startswith("error")


def test_request_wiki_update_is_a_buildable_tool():
    assert "request_wiki_update" in {t.name for t in build_tools(["request_wiki_update"])}
