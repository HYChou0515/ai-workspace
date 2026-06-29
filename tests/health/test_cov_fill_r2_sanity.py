"""Cov-fill: exercise ``SanityBatteryCoordinator.consuming`` (the #312
``run_consumers``-gate observable). Pure-unit: a minimal coordinator over a
fresh spec + a fake ``ILlm`` factory — no real LLM / docker / network. The
``consuming`` property's ``return self._consuming`` line was only hit by flaky
LLM-path integration runs; this makes it deterministic."""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.health.sanity.coordinator import SanityBatteryCoordinator
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec


class _FakeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield "ok", False


def _factory(model: str, level: str) -> ILlm:
    return _FakeLlm()


def test_consuming_property_reflects_lifecycle() -> None:
    coord = SanityBatteryCoordinator(make_spec(), _factory)
    # the getter executes `return self._consuming` — False before any start
    assert coord.consuming is False
    coord.start_consuming()
    # …and flips once the background consumer is wired
    assert coord.consuming is True
