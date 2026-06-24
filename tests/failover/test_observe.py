"""The failover switch logger (#196 observability)."""

from __future__ import annotations

import logging

from workspace_app.failover.observe import make_switch_logger


def test_switch_logger_warns_with_role_model_and_cause(caplog):
    on_switch = make_switch_logger("kb-llm")
    with caplog.at_level(logging.WARNING, logger="workspace_app.failover"):
        on_switch("qwen3:14b", RuntimeError("500 busy"))
    assert "kb-llm" in caplog.text
    assert "qwen3:14b" in caplog.text
    assert "500 busy" in caplog.text
