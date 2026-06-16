"""Gate + install the LLM call logger (observability feature B).

`install_llm_logging` is called once at startup. When the logger is enabled it
constructs the writer + `LlmCallLogger` and registers it into
`litellm.callbacks`, so every subsequent litellm call is recorded.

Gating precedence: the `WORKSPACE_LLM_LOG` env var (the prod off-switch) wins
over the config `observability.llm_log.enabled` (which ships TRUE).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ..config.schema import Settings
from .logger import LlmCallLogger
from .writer import LlmLogWriter

ENV_FLAG = "WORKSPACE_LLM_LOG"
_TRUTHY = ("1", "true", "yes", "on")


def llm_log_enabled(settings: Settings, env: Mapping[str, str]) -> bool:
    """Whether to record LLM calls. `WORKSPACE_LLM_LOG` (truthy → on, anything
    else → off) overrides config; unset → the config default."""
    raw = env.get(ENV_FLAG)
    if raw is not None:
        return raw.strip().lower() in _TRUTHY
    return settings.observability.llm_log.enabled


def install_llm_logging(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    *,
    root: Path | None = None,
) -> LlmCallLogger | None:
    """Register the LLM call logger into `litellm.callbacks` when enabled.
    Returns the logger (or None when disabled). `root` overrides the configured
    log directory (used by tests)."""
    e = os.environ if env is None else env
    if not llm_log_enabled(settings, e):
        return None
    log_root = Path(root) if root is not None else Path(settings.observability.llm_log.dir)
    logger = LlmCallLogger(LlmLogWriter(log_root))

    import litellm

    litellm.callbacks.append(logger)
    return logger
