"""#513 P1 — the ``lookup_defect`` agent tool: a deterministic, station-scoped
defect-library lookup beside ``lookup_glossary``. It resolves a code within a
caller-supplied scope chain (most-specific first) via ``defect_library.resolve``
and surfaces the matched entry as authoritative context."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from agents import RunContextWrapper
from specstar.types import Binary

from workspace_app.agent import AgentToolContext, build_tools, lookup_defect_impl
from workspace_app.agent.tools import classify_defect_image_impl
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.defect_library import scope_key
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard, SourceDoc


def _coll(spec, name: str = "Defects") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _card(spec, cid: str, keys: list[str], body: str) -> str:
    return (
        spec.get_resource_manager(ContextCard)
        .create(
            ContextCard(
                collection_id=cid,
                keys=keys,
                norm_keys=derive_norm_keys(keys),
                title=keys[0],
                body=body,
            )
        )
        .resource_id
    )


def test_lookup_defect_surfaces_the_scoped_entry():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    _card(spec, cid, [scope_key("etch", "M4")], "Bridge morphology at etch.")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    out = lookup_defect_impl(ctx, "M4", ["etch"])
    assert "Bridge morphology at etch." in out  # the entry body is surfaced as authoritative


def test_lookup_defect_prefers_the_machine_override():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    _card(spec, cid, [scope_key("etch", "M4")], "shared etch entry")
    _card(spec, cid, [scope_key("etchtool07", "M4")], "tool-07 override")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    out = lookup_defect_impl(ctx, "M4", ["etchtool07", "etch"])
    assert "tool-07 override" in out
    assert "shared etch entry" not in out  # the broader entry is not surfaced


def test_lookup_defect_reports_a_miss():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    assert "No defect entry found" in lookup_defect_impl(ctx, "M4", ["etch"])


def test_lookup_defect_without_a_spec_errors():
    ctx = RunContextWrapper(AgentToolContext(collection_ids=["c1"]))
    out = lookup_defect_impl(ctx, "M4", ["etch"])
    assert out.startswith("error:")
    assert "collection-scoped context" in out


def test_lookup_defect_is_a_buildable_tool():
    assert "lookup_defect" in {t.name for t in build_tools(["lookup_defect"])}


def test_lookup_defect_surfaces_the_card_id_for_read_before_write():
    # The flywheel updates entries, so the lookup output must carry the hit's id.
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    rid = _card(spec, cid, [scope_key("etch", "M4")], "bridge")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    assert rid in lookup_defect_impl(ctx, "M4", ["etch"])


# --- P3: classify_defect_image (use case B) --------------------------------

# A real minimal 1×1 PNG — libmagic must sniff it as image/png (the tool rejects
# non-images), so a fake header won't do.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00"
    b"\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeVlm(IVlm):
    """A vision model that records the question + images it was handed and yields
    a canned answer — so the tool↔describer contract runs end-to-end (like the
    read_image tool test), not through a hand-rolled describer double."""

    def __init__(self, answer: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._answer = answer

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield self._answer, False


def _describer(answer: str) -> VlmDescriber:
    return VlmDescriber(_FakeVlm(answer))


def _image_doc(spec, cid: str, data: bytes = _PNG, path: str = "query.png") -> str:
    rm = spec.get_resource_manager(SourceDoc)
    doc = SourceDoc(collection_id=cid, path=path, content=Binary(data=data), status="ready")
    return rm.create(doc).resource_id


def test_classify_defect_ranks_the_image_against_in_scope_entries():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    _card(spec, cid, [scope_key("etch", "scratch")], "a linear gouge across the die")
    _card(spec, cid, [scope_key("etch", "M4")], "a metal-4 bridge")
    _card(spec, cid, [scope_key("litho", "haze")], "off-scope, another station")
    doc_id = _image_doc(spec, cid)
    vlm = _FakeVlm("Most likely: scratch — a clear linear gouge is visible.")
    ctx = RunContextWrapper(
        AgentToolContext(spec=spec, collection_ids=[cid], describer=VlmDescriber(vlm))
    )
    out = classify_defect_image_impl(ctx, doc_id, ["etchtool07", "etch"], context="wafer edge")

    assert "scratch" in out.lower()  # the VLM's ranked answer is surfaced
    prompt = vlm.calls[0]["prompt"]
    assert vlm.calls[0]["images"] == [(_PNG, "image/png")]  # the uploaded bytes reached the VLM
    # the prompt hard-filtered to the two etch entries; the litho one is absent
    assert isinstance(prompt, str)
    assert "a linear gouge across the die" in prompt
    assert "a metal-4 bridge" in prompt
    assert "off-scope" not in prompt
    assert "wafer edge" in prompt  # the user's context rode along


def test_classify_defect_reports_no_candidates_in_scope():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    _card(spec, cid, [scope_key("litho", "haze")], "only at litho")
    doc_id = _image_doc(spec, cid)
    ctx = RunContextWrapper(
        AgentToolContext(spec=spec, collection_ids=[cid], describer=_describer("x"))
    )
    out = classify_defect_image_impl(ctx, doc_id, ["etch"])
    assert "No defect entries in scope" in out  # nothing to compare against → say so


def test_classify_defect_without_a_vision_model_errors():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    doc_id = _image_doc(spec, cid)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))  # describer=None
    out = classify_defect_image_impl(ctx, doc_id, ["etch"])
    assert out.startswith("error:") and "vision model" in out


def test_classify_defect_rejects_a_non_image_document():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    doc_id = _image_doc(spec, cid, data=b"# just markdown, not an image\n", path="notes.md")
    ctx = RunContextWrapper(
        AgentToolContext(spec=spec, collection_ids=[cid], describer=_describer("x"))
    )
    out = classify_defect_image_impl(ctx, doc_id, ["etch"])
    assert out.startswith("error:") and "not an image" in out


def test_classify_defect_is_a_buildable_tool():
    assert "classify_defect" in {t.name for t in build_tools(["classify_defect"])}
