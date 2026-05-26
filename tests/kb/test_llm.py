"""ILlm.collect: drains the stream, forwards every chunk (live thinking), and
returns only the non-reasoning content."""

from collections.abc import Iterator

from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, chunks: list[tuple[str, bool]]) -> None:
        self._chunks = chunks

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield from self._chunks


def test_collect_returns_content_and_forwards_all_chunks():
    llm = _FakeLlm([("<think>", True), ("hmm", True), ("hello ", False), ("world", False)])
    seen: list[tuple[str, bool]] = []
    text = llm.collect("p", on_chunk=lambda t, r: seen.append((t, r)))
    assert text == "hello world"  # reasoning chunks excluded from the result
    assert seen == [("<think>", True), ("hmm", True), ("hello ", False), ("world", False)]


def test_collect_without_callback_still_works():
    assert _FakeLlm([("a", False), ("b", False)]).collect("p") == "ab"
