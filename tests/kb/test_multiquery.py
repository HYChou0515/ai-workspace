from workspace_app.kb.llm import LitellmLlm
from workspace_app.kb.query import expand_queries


class _FakeLlm:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._reply


def test_expand_prepends_original_and_parses_variants():
    llm = _FakeLlm("- reflow oven drift\n- zone 3 temperature\n2. solder void cause")
    out = expand_queries(llm, "why solder voids", n=3)
    assert out[0] == "why solder voids"  # original always first
    assert "reflow oven drift" in out  # bullet stripped
    assert "zone 3 temperature" in out
    assert "solder void cause" in out  # numbered prefix stripped
    assert len(out) == 4  # original + 3 variants


def test_expand_dedups_and_caps_to_n_variants():
    llm = _FakeLlm("why solder voids\nsame\nsame\nanother")
    out = expand_queries(llm, "why solder voids", n=2)
    # original echoed by the model is not duplicated; capped at original + 2
    assert out == ["why solder voids", "same", "another"]


def test_expand_with_fewer_variants_than_n():
    out = expand_queries(_FakeLlm("only one variant"), "orig", n=3)
    assert out == ["orig", "only one variant"]  # loop exhausts; cap never reached


def test_litellm_llm_constructs():
    assert isinstance(LitellmLlm("ollama_chat/qwen3:14b"), LitellmLlm)


def test_hypothetical_document_returns_the_stripped_completion():
    from workspace_app.kb.query import hypothetical_document

    llm = _FakeLlm("  Reflow zone three drifted, causing solder voids.\n")
    doc = hypothetical_document(llm, "why solder voids")
    assert doc == "Reflow zone three drifted, causing solder voids."
    assert "why solder voids" in llm.prompts[0]  # the query seeds the hypothesis


def test_hypothetical_document_empty_completion_is_empty():
    from workspace_app.kb.query import hypothetical_document

    assert hypothetical_document(_FakeLlm("   "), "q") == ""
