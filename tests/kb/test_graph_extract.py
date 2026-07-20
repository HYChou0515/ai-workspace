from collections.abc import Iterator

from workspace_app.kb.graph.extract import MetricClaim, extract_claims
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


def test_extract_claims_parses_the_models_metric_list_and_shows_it_the_text():
    llm = _FakeLlm(
        '[{"metric": "營收", "period": "FY24 Q3", "value": "1.2M", "unit": "USD"},'
        ' {"metric": "員工數", "period": "", "value": "340", "unit": "人"}]'
    )
    claims = extract_claims(llm, "FY24 Q3 營收 1.2M 美元,員工 340 人。")
    assert claims == [
        MetricClaim(metric="營收", period="FY24 Q3", value="1.2M", unit="USD"),
        MetricClaim(metric="員工數", period="", value="340", unit="人"),
    ]
    assert "FY24 Q3 營收 1.2M 美元,員工 340 人。" in llm.prompts[0]


def test_extract_claims_returns_empty_when_no_metrics():
    assert extract_claims(_FakeLlm("[]"), "a slide with no numbers") == []


def test_extract_claims_survives_a_non_json_reply():
    assert extract_claims(_FakeLlm("I could not find any metrics."), "x") == []


def test_extract_claims_strips_markdown_fences_and_preamble():
    llm = _FakeLlm('Here you go:\n```json\n[{"metric": "良率", "value": "98%"}]\n```')
    assert extract_claims(llm, "yield 98%") == [MetricClaim(metric="良率", value="98%")]


def test_extract_claims_drops_entries_without_a_metric_name():
    llm = _FakeLlm('[{"metric": "", "value": "5"}, {"value": "9"}, {"metric": "x"}]')
    assert extract_claims(llm, "t") == [MetricClaim(metric="x")]
