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
    #: The half has a ``validate`` command (#847/#848 P9). ``show_file`` then
    #: runs ``launch validate {"path": <workspace-relative .ai.yaml>}`` before
    #: showing one of this plugin's views: a non-zero exit refuses it (stderr is
    #: the reason), a zero exit's first stdout line is appended to the reply.
    validate: bool = False


class Provides(Struct, forbid_unknown_fields=True):
    """Platform capabilities a plugin's sandbox half serves, each named by the
    sandbox command that serves it. The platform finds a capability's plugin
    here and never by the plugin's name.

    - ``marking_rows`` — "save a marking as a table" (plan-view-plugins-pr5
      P7). The command takes ``{"view": <workspace path of a view file or a
      table file>}`` plus exactly one of ``{"columns": {column: [text, …]}}`` or
      ``{"marking": <workspace path of a .markings/<name>.json>}``, and answers
      ``{"rows": n, "csv": text, "columns": {column: [text, …]}}`` — the view's
      rows the marking lights, every column, and the marking it lit them by (as
      given or as read from the file; the platform checks a chat chip's save
      against it) — or exits 2 with one user-facing sentence on stderr. At most one
      installed plugin may provide it (``discovery`` refuses two).
    """

    marking_rows: str | None = None


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
    provides: Provides | None = None
