"""The ``plugin.json`` schema (#847/#848 PR1 P2).

Unknown keys are rejected, the same rule as the config loader: a misspelt key
that silently does nothing is the failure this closes. Cross-field rules (a
``views`` entry naming one of the plugin's own kinds, exactly one sandbox
source) are checked in ``discovery`` where the plugin's name is at hand for the
message.
"""

from __future__ import annotations

from msgspec import Struct, field


class ViewEntry(Struct, forbid_unknown_fields=True):
    """One line of the agent-facing ``## Available views`` index: which kind,
    and when an agent should reach for it (stated as a capability)."""

    kind: str
    when: str


class SandboxHalf(Struct, forbid_unknown_fields=True):
    """Where the plugin's sandbox commands come from — exactly one of:

    - ``bundle``: a prebuilt tool bundle inside the plugin folder (relative
      path). Under ``sandbox.kind: local`` boot copies it into the merged tools
      root; under ``kind: http`` it must already be in sandbox-host ``builtin/``.
    - ``artifact``: a #674 artifact URL, resolved by the hosted sandbox like an
      app's ``external_tools``.
    """

    bundle: str | None = None
    artifact: str | None = None


class PluginManifest(Struct, forbid_unknown_fields=True):
    name: str
    #: The SDK major version the plugin was built against. The SPA refuses a
    #: plugin whose major differs from its own, per panel, loudly.
    sdk: str
    kinds: list[str]
    views: list[ViewEntry] = field(default_factory=list)
    #: Relative path of a folder holding ``SKILL.md``.
    skill: str | None = None
    sandbox: SandboxHalf | None = None
