"""Issue #479: the coordinator's reflect op — a PROSE wiki collection can be
reflected (survey → plan → apply) via a durable ``reflect`` job. Enqueue is a
no-op for code / non-wiki collections, coalesces, and records a misconfig when no
wiki LLM is wired; the handler runs the reflector, journals the pass, and stamps
``last_reflected_at``, swallowing failures so the partition never wedges."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from specstar import QB

from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.kb.wiki.jobs import WikiMaintenanceJob
from workspace_app.kb.wiki.store import WikiFileStore, reflection_page_path
from workspace_app.resources import Collection, make_spec


class _Llm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ("a summary.", False)  # never valid JSON → an empty (no-op) plan


class _BoomLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        raise RuntimeError("model exploded")
        yield ("", False)  # pragma: no cover — unreachable; makes this a generator


class _NoopRunner:
    async def run(self, prompt, ctx):  # the reflect path never uses the agent runner
        if False:  # pragma: no cover
            yield


def _prose(spec, *, use_wiki: bool = True, git_url: str | None = None) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="kb", use_wiki=use_wiki, git_url=git_url))
        .resource_id
    )


def _jobs(spec) -> list:
    return [
        r.data
        for r in spec.get_resource_manager(WikiMaintenanceJob).list_resources(QB.all().build())
    ]


_FIXED_NOW = lambda: datetime(2026, 7, 6, tzinfo=UTC)  # noqa: E731


async def test_enqueue_reflect_creates_a_reflect_job_for_a_prose_wiki():
    spec = make_spec(default_user="u")
    cid = _prose(spec)
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    coord.enqueue_reflect(cid, requested_by="u")  # explicit requester branch
    assert [j.payload.op for j in _jobs(spec)] == ["reflect"]


async def test_enqueue_reflect_is_a_noop_for_code_or_non_wiki_or_missing():
    spec = make_spec(default_user="u")
    code = _prose(spec, git_url="https://g/r.git")
    off = _prose(spec, use_wiki=False)
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    coord.enqueue_reflect(code)
    coord.enqueue_reflect(off)
    coord.enqueue_reflect("missing")
    assert _jobs(spec) == []


async def test_enqueue_reflect_coalesces_onto_one_in_flight_reflection():
    spec = make_spec(default_user="u")
    cid = _prose(spec)
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    coord.enqueue_reflect(cid)
    coord.enqueue_reflect(cid)  # a reflection is already queued → coalesced
    assert len(_jobs(spec)) == 1


async def test_enqueue_reflect_records_a_misconfig_without_a_wiki_llm():
    spec = make_spec(default_user="u")
    cid = _prose(spec)
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner())  # no wiki LLM
    coord.enqueue_reflect(cid)
    assert _jobs(spec) == []
    assert "wiki LLM not configured" in (coord.status(cid).last_error or "")


async def test_reflect_runs_journals_the_pass_and_stamps_last_reflected():
    spec = make_spec(default_user="u")
    cid = _prose(spec)
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/x.md", b"# X\n\nthe only page.\n")
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm(), now=_FIXED_NOW)
    coord.enqueue_reflect(cid)
    await coord.aclose()  # the consumer runs the reflection
    assert await store.exists(cid, reflection_page_path("2026-07-06"))
    coll = spec.get_resource_manager(Collection).get(cid).data
    assert isinstance(coll, Collection)
    assert coll.last_reflected_at == "2026-07-06T00:00:00+00:00"
    assert coord.status(cid).building is False


async def test_reflect_failure_is_recorded_not_wedged():
    spec = make_spec(default_user="u")
    cid = _prose(spec)
    await WikiFileStore(spec).write(cid, "/entities/x.md", b"# X\n\np.\n")
    coord = WikiMaintenanceCoordinator(
        spec, _NoopRunner(), code_wiki_llm=_BoomLlm(), now=_FIXED_NOW
    )
    coord.enqueue_reflect(cid)
    await coord.aclose()
    st = coord.status(cid)
    assert st.errors >= 1
    assert "reflection run failed" in (st.last_error or "")
    assert st.building is False
