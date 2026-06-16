"""The seven bundled capability probes — pass / fail / skip per check,
driven by fakes. Live behaviour is the check's whole point, so these
tests pin the ASSERTION LOGIC (what counts as pass/fail), not the
model: the canned probe inputs are exercised against scripted fakes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import SimpleNamespace

from workspace_app.health.checks import (
    EmbedderDimCheck,
    InsightExtractionCheck,
    RetrievalExpandCheck,
    ToolCallCheck,
    VlmDescribeCheck,
)
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.kb.vlm import IVlm


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


class _FakeVlm(IVlm):
    def __init__(self, response: str) -> None:
        self._response = response
        self.images: list[tuple[bytes, str]] = []

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.images.extend(images)
        yield (self._response, False)


# ── embedders ────────────────────────────────────────────────────────


def test_embedder_dim_check_passes_on_matching_dim():
    c = EmbedderDimCheck(
        HashEmbedder(dim=16), expected_dim=16, check_id="embedder-default", description="d"
    )
    assert c.fast is True  # connectivity-grade → startup sync set
    assert c.run().status == "pass"


def test_embedder_dim_check_fails_on_mismatch_with_actionable_detail():
    c = EmbedderDimCheck(
        HashEmbedder(dim=8), expected_dim=16, check_id="embedder-default", description="d"
    )
    res = c.run()
    assert res.status == "fail"
    assert "8" in res.detail and "16" in res.detail


def test_embedder_dim_check_skips_when_unconfigured():
    c = EmbedderDimCheck(None, expected_dim=16, check_id="embedder-code", description="d")
    assert c.run().status == "skip"


# ── kb llm probes ────────────────────────────────────────────────────


def test_insight_extraction_check_passes_when_model_extracts():
    llm = _FakeLlm(
        '{"insights": [{"kind": "root_cause", "title": "Zone-3 drift",'
        ' "markdown": "# RC\\n\\nThermocouple."}]}'
    )
    res = InsightExtractionCheck(llm).run()
    assert res.status == "pass"
    assert "root_cause" in res.detail


def test_insight_extraction_check_fails_on_unparseable_model_output():
    """The qwen3:14b incident as a check: model rambles → fail with a
    detail that points at the consequence."""
    res = InsightExtractionCheck(_FakeLlm("sure! here are my thoughts…")).run()
    assert res.status == "fail"
    assert "no parseable insights" in res.detail


def test_insight_extraction_check_skips_without_llm():
    assert InsightExtractionCheck(None).run().status == "skip"


def test_retrieval_expand_check_pass_and_fail():
    assert RetrievalExpandCheck(_FakeLlm("alt phrasing one\nalt two")).run().status == "pass"
    res = RetrievalExpandCheck(_FakeLlm("")).run()
    assert res.status == "fail"
    assert RetrievalExpandCheck(None).run().status == "skip"


# ── vlm probe ────────────────────────────────────────────────────────


def test_vlm_describe_check_passes_when_probe_text_is_read():
    """The probe image carries rendered text — the capability ingestion
    actually relies on is reading it back (screenshots / slides / scans),
    not naming colours (live finding: qwen2.5vl-via-Ollama hallucinates
    on featureless synthetic images while reading text-bearing ones
    fine — a colour probe over-alarms)."""
    vlm = _FakeVlm("**Verbatim transcription**\n\nREFLOW ZONE 3\nsetpoint 245C")
    res = VlmDescribeCheck(vlm).run()
    assert res.status == "pass"
    # A real PNG reached the model.
    assert vlm.images and vlm.images[0][0][:8] == b"\x89PNG\r\n\x1a\n"
    assert vlm.images[0][1] == "image/png"


def test_vlm_describe_check_fails_when_model_cannot_read_the_text():
    """The qwen2.5vl incident as a check: the model returns a fluent
    hallucination instead of the probe text → fail, with the
    consequence and the actual output in the detail."""
    res = VlmDescribeCheck(_FakeVlm("a simple line graph with a single line")).run()
    assert res.status == "fail"
    assert "line graph" in res.detail


def test_vlm_describe_check_skips_without_vlm():
    assert VlmDescribeCheck(None).run().status == "skip"


# ── agent tool-calling probe ─────────────────────────────────────────


def _chunk(content=None, tool_name=None):
    tool_calls = None
    if tool_name is not None:
        tool_calls = [SimpleNamespace(function=SimpleNamespace(name=tool_name))]
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def test_tool_call_check_passes_when_the_model_calls_the_tool(monkeypatch):
    import litellm

    monkeypatch.setattr(
        litellm, "completion", lambda **kw: iter([_chunk(tool_name="lookup"), _chunk()])
    )
    res = ToolCallCheck(
        check_id="agent-workspace", description="d", model="ollama_chat/qwen3:14b"
    ).run()
    assert res.status == "pass"


def test_tool_call_check_fails_when_the_model_answers_in_prose(monkeypatch):
    import litellm

    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **kw: iter([_chunk(content="Reflow is a soldering process where…")]),
    )
    res = ToolCallCheck(check_id="agent-kb-chat", description="d", model="m").run()
    assert res.status == "fail"
    assert "narrate" in res.detail
    assert "Reflow is a soldering" in res.detail


def test_tool_call_check_streams_with_the_probe_tool(monkeypatch):
    """Pins the call shape: stream=True (always-stream rule) and exactly
    one synthetic 'lookup' tool offered."""
    import litellm

    seen: dict = {}

    def fake_completion(**kw):
        seen.update(kw)
        return iter([_chunk(tool_name="lookup")])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    ToolCallCheck(check_id="x", description="d", model="m", base_url="http://x").run()
    assert seen["stream"] is True
    assert seen["api_base"] == "http://x"
    assert [t["function"]["name"] for t in seen["tools"]] == ["lookup"]


def test_tool_call_check_skips_without_model():
    assert ToolCallCheck(check_id="x", description="d", model=None).run().status == "skip"


def test_tool_call_check_can_probe_a_custom_tool(monkeypatch):
    """A parameterised probe (the wiki reader's search_wiki) offers THAT tool
    and asserts the model calls it — not the default lookup."""
    import litellm

    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        return iter([_chunk(tool_name="search_wiki")])

    monkeypatch.setattr(litellm, "completion", fake)
    res = ToolCallCheck(
        check_id="agent-wiki-reader",
        description="d",
        model="m",
        tool_name="search_wiki",
        param_name="query",
        prompt="search the wiki",
    ).run()
    assert res.status == "pass"
    assert [t["function"]["name"] for t in seen["tools"]] == ["search_wiki"]
    assert set(seen["tools"][0]["function"]["parameters"]["properties"]) == {"query"}


def test_custom_tool_probe_fails_when_a_different_tool_is_called(monkeypatch):
    """Calling some OTHER tool than the one probed is a fail — the model didn't
    exercise the capability under test."""
    import litellm

    monkeypatch.setattr(litellm, "completion", lambda **kw: iter([_chunk(tool_name="lookup")]))
    res = ToolCallCheck(
        check_id="agent-wiki-maintainer", description="d", model="m", tool_name="write_file"
    ).run()
    assert res.status == "fail"
