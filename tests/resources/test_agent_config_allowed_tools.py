"""AgentConfig.allowed_tools tri-state semantics.

Three distinguishable states (Q4-followup of the config-refactor grill):

- ``None``  — "I haven't specified" → runner exposes the default
  workspace toolset. This is what a bare ``AgentConfig(name=...)``
  yields, and what bundled RCA presets carry (so picking one gives
  the operator the standard agent).
- ``[]``    — "I have explicitly specified zero tools" → runner
  exposes NO tools. The KB chat misconfig that motivated this fix:
  a preset without ``allowed_tools`` was silently treated as "all
  defaults" by the runner's old ``or None`` aliasing, so a kb_chat
  pointed at an RCA preset got every workspace tool except kb_search.
- ``[...]`` — "exactly these" → runner exposes only the named tools.

This module pins the schema-level contract; the runner-level fix
(removing ``or None``) is tested under tests/api/.
"""

from __future__ import annotations

from workspace_app.resources import AgentConfig


def test_bare_agent_config_defaults_allowed_tools_to_none():
    """A construction-without-arg AgentConfig carries the "haven't
    specified" signal so the runner can fall back to its workspace
    defaults. Previously this defaulted to ``[]``, which collided with
    the explicit "no tools" intent under the runner's ``or None``
    aliasing."""
    cfg = AgentConfig(name="x")
    assert cfg.allowed_tools is None


def test_explicit_empty_list_is_preserved():
    """The whole reason the default changed: an explicit ``[]`` must
    mean "no tools" without being silently aliased to None."""
    cfg = AgentConfig(name="x", allowed_tools=[])
    assert cfg.allowed_tools == []
    assert cfg.allowed_tools is not None


def test_explicit_list_is_preserved_verbatim():
    cfg = AgentConfig(name="x", allowed_tools=["exec", "read_file"])
    assert cfg.allowed_tools == ["exec", "read_file"]
