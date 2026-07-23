"""#630 — attribute extraction: 「誰 · 什麼屬性 · 值」, whatever the value is.

The old prompt asked only for metrics "that carry a NUMERIC value", which made
the value's TYPE the gate: a stated recipe, a supplier, a mode name never
arrived. Nothing in the field does it that way (see the #628 survey), and the
type tells you nothing about whether the statement matters. Two things change:

* any value — "98.7", "PPOOIXUX", "Ar/O2" — is an attribute statement;
* every statement names WHOSE attribute it is, so a figure binds to its subject
  by what the passage said, not by which slide it happened to share.
"""

from collections.abc import Iterator

from workspace_app.kb.graph.extract import AttributeClaim, extract_claims
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


def test_extract_parses_subject_attribute_value_and_shows_the_model_the_text():
    llm = _FakeLlm(
        '[{"subject": "回焊爐", "attribute": "良率", "period": "FY24 Q3",'
        ' "value": "98.7", "unit": "%"},'
        ' {"subject": "產線三", "attribute": "員工數", "period": "",'
        ' "value": "340", "unit": "人"}]'
    )
    claims = extract_claims(llm, "FY24 Q3 回焊爐良率 98.7%,產線三員工 340 人。")
    assert claims == [
        AttributeClaim(
            subject="回焊爐", attribute="良率", period="FY24 Q3", value="98.7", unit="%"
        ),
        AttributeClaim(subject="產線三", attribute="員工數", period="", value="340", unit="人"),
    ]
    assert "FY24 Q3 回焊爐良率 98.7%,產線三員工 340 人。" in llm.prompts[0]


def test_a_textual_setting_is_an_attribute_like_any_other():
    """The whole point of #630: this used to be unrepresentable."""
    llm = _FakeLlm('[{"subject": "N5 TV0", "attribute": "recipe", "value": "PPOOIXUX"}]')
    assert extract_claims(llm, "N5 TV0 的 recipe 是 PPOOIXUX。") == [
        AttributeClaim(subject="N5 TV0", attribute="recipe", value="PPOOIXUX")
    ]


def test_the_prompt_never_asks_for_numbers():
    """A guard on the gate itself — the regression this issue exists to prevent."""
    llm = _FakeLlm("[]")
    extract_claims(llm, "t")
    prompt = llm.prompts[0].lower()
    assert "numeric" not in prompt
    assert "number" not in prompt


def test_extract_claims_returns_empty_when_nothing_is_stated():
    assert extract_claims(_FakeLlm("[]"), "a slide that states no attributes") == []


def test_extract_claims_survives_a_non_json_reply():
    assert extract_claims(_FakeLlm("I could not find anything."), "x") == []


def test_extract_claims_strips_markdown_fences_and_preamble():
    llm = _FakeLlm(
        'Here you go:\n```json\n[{"subject": "爐", "attribute": "良率", "value": "98%"}]\n```'
    )
    assert extract_claims(llm, "yield 98%") == [
        AttributeClaim(subject="爐", attribute="良率", value="98%")
    ]


def test_a_statement_missing_its_subject_or_attribute_is_dropped():
    """Both are load-bearing: with no subject the statement cannot be filed under
    anything, and with no attribute name it says nothing."""
    llm = _FakeLlm(
        '[{"subject": "", "attribute": "良率", "value": "5"},'
        ' {"attribute": "良率", "value": "9"},'
        ' {"subject": "爐", "attribute": "", "value": "1"},'
        ' {"subject": "爐", "attribute": "良率", "value": "98"}]'
    )
    assert extract_claims(llm, "t") == [AttributeClaim(subject="爐", attribute="良率", value="98")]


def test_a_statement_with_no_value_is_dropped():
    """「X 的 recipe 是 ___」 carries nothing — an attribute with no value is noise."""
    llm = _FakeLlm('[{"subject": "爐", "attribute": "recipe", "value": ""}]')
    assert extract_claims(llm, "t") == []
