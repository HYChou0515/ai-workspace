"""Runtime view plugins (#847/#848).

A view plugin is one folder an OPERATOR installs under ``view_plugins.dir``
(default ``<repo>/.view-plugins``, env ``WORKSPACE_VIEW_PLUGINS_DIR``)::

    <name>/
      plugin.json     # the manifest (see ``manifest.PluginManifest``)
      web/index.js    # built ES module; registers its view kinds through the SDK
      sandbox/        # optional: a prebuilt tool bundle (its `launch` runs commands)
      skill/SKILL.md  # optional: a shared skill for agents that can draw views
      scenarios/      # optional: skill_eval scenarios for `view_plugin tune`

Plugins run in the SPA origin with the signed-in user's full rights, so they are
operator-installed and operator-trusted, never user-uploaded. See
``docs/view-kind-authoring.md``.
"""

from workspace_app.view_plugins.discovery import (
    BUILTIN_VIEW_KINDS,
    ViewPlugin,
    ViewPluginError,
    discover_view_plugins,
    marking_rows_provider,
)
from workspace_app.view_plugins.manifest import PluginManifest, Provides, SandboxHalf, ViewEntry

__all__ = [
    "BUILTIN_VIEW_KINDS",
    "PluginManifest",
    "Provides",
    "SandboxHalf",
    "ViewEntry",
    "ViewPlugin",
    "ViewPluginError",
    "discover_view_plugins",
    "marking_rows_provider",
]
