"""Discover the installed view plugins (#847/#848 PR1 P2).

Strict, like ``tooling.registry.discover_packages``: every folder under the
plugin dir must be a complete, well-formed plugin, and anything else refuses
boot with a message naming the plugin and the field. A half-installed plugin
that is skipped quietly becomes "Unsupported view kind" in a user's panel,
which nobody traces back to the operator's install. A missing dir, though, is
simply "no plugins": the default dir does not exist in most deployments.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import msgspec

from workspace_app.config.schema import ViewPluginsSettings
from workspace_app.view_plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

#: Kinds the SPA owns: the registry's built-ins (`table`, `board`, `gantt`) and
#: the names the container reserves (`health`, `wui`). Mirrors `VIEW_KIND` in
#: `web/src/renderers/entity/types.ts` — `test_builtin_kinds_match_the_spa`
#: reads that file, so the two cannot drift apart silently.
BUILTIN_VIEW_KINDS = frozenset({"table", "board", "gantt", "health", "wui"})

#: A plugin's name is a URL path segment (`/api/view-plugins/{name}/…`) and a
#: folder name, so it is a lowercase slug.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
#: `sdk` is a major version, e.g. "1".
_SDK = re.compile(r"^[0-9]+$")

_REPO = Path(__file__).resolve().parents[3]
#: Mirrors ``tooling.packages.PREBUILT_DIR`` (``<repo>/.workspace-tools``).
DEFAULT_PLUGINS_DIR = _REPO / ".view-plugins"
ENV_VAR = "WORKSPACE_VIEW_PLUGINS_DIR"

MANIFEST = "plugin.json"
WEB_ENTRY = Path("web") / "index.js"


class ViewPluginError(RuntimeError):
    """A plugin the platform refuses to boot with. The message names it."""


@dataclass(frozen=True)
class ViewPlugin:
    name: str
    dir: Path
    manifest: PluginManifest

    @property
    def skill_dir(self) -> Path | None:
        return None if self.manifest.skill is None else self.dir / self.manifest.skill

    @property
    def web_dir(self) -> Path:
        return self.dir / "web"


def _refuse(name: str, where: Path, why: str) -> ViewPluginError:
    return ViewPluginError(f"view plugin {name!r} ({where}): {why}")


def _inside(base: Path, rel: str) -> bool:
    target = (base / rel).resolve()
    return target == base.resolve() or base.resolve() in target.parents


def load_view_plugin(sub: Path) -> ViewPlugin:
    folder = sub.name
    path = sub / MANIFEST

    def bad(why: str, where: Path = path) -> ViewPluginError:
        return _refuse(folder, where, why)

    if not path.is_file():
        raise bad(f"{MANIFEST} is missing", sub)
    try:
        m = msgspec.json.decode(path.read_bytes(), type=PluginManifest)
    except msgspec.DecodeError as e:  # ValidationError subclasses it: unknown key, wrong type
        raise bad(str(e)) from e
    if not _NAME.match(m.name):
        raise bad(f"name {m.name!r} must be a lowercase slug ([a-z0-9][a-z0-9_-]*)")
    if m.name != folder:
        raise bad(f"name {m.name!r} must equal its folder name {folder!r}")
    if not _SDK.match(m.sdk):
        raise bad(f'sdk {m.sdk!r} must be a major version string, e.g. "1"')
    if not m.kinds:
        raise bad("kinds must list at least one view kind")
    seen: set[str] = set()
    for kind in m.kinds:
        if not kind.strip():
            raise bad("kinds may not contain an empty name")
        if kind in seen:
            raise bad(f"kinds lists {kind!r} twice")
        if kind in BUILTIN_VIEW_KINDS:
            raise bad(f"kinds: {kind!r} is a built-in view kind — pick another name")
        seen.add(kind)
    for i, v in enumerate(m.views):
        if v.kind not in seen:
            raise bad(f"views[{i}].kind {v.kind!r} is not one of this plugin's kinds {m.kinds}")
        if not v.when.strip():
            raise bad(f"views[{i}].when is empty — say when an agent should use {v.kind!r}")
    if m.skill is not None:
        if not _inside(sub, m.skill):
            raise bad(f"skill {m.skill!r} must be a folder inside the plugin")
        if not (sub / m.skill / "SKILL.md").is_file():
            raise bad(f"skill {m.skill!r} has no SKILL.md")
    if m.sandbox is not None:
        sources = [s for s in (m.sandbox.bundle, m.sandbox.artifact) if s]
        if len(sources) != 1:
            raise bad("sandbox must name exactly one of `bundle` or `artifact`")
        if m.sandbox.bundle is not None and not _inside(sub, m.sandbox.bundle):
            raise bad(f"sandbox.bundle {m.sandbox.bundle!r} must be a folder inside the plugin")
    if not (sub / WEB_ENTRY).is_file():
        raise bad(f"{WEB_ENTRY.as_posix()} is missing — build the plugin's web half", sub)
    return ViewPlugin(name=m.name, dir=sub, manifest=m)


def resolve_plugins_dir(
    settings: ViewPluginsSettings, env: Mapping[str, str] | None = None
) -> Path:
    """``view_plugins.dir`` if set, else ``$WORKSPACE_VIEW_PLUGINS_DIR``, else
    ``<repo>/.view-plugins``."""
    if settings.dir:
        return Path(settings.dir)
    e = os.environ if env is None else env
    return Path(e[ENV_VAR]) if e.get(ENV_VAR) else DEFAULT_PLUGINS_DIR


def is_furniture(name: str) -> bool:
    """A folder in the plugin dir that is the filesystem's, not a plugin: a
    dot-folder (`.snapshot` on a NAS) or ext4's `lost+found`, which every freshly
    formatted volume mounted as the plugin dir carries. A plugin name can be
    neither (it is a `[a-z0-9]…` slug)."""
    return name.startswith(".") or name == "lost+found"


def discover_view_plugins(plugins_dir: Path) -> list[ViewPlugin]:
    """Every plugin under ``plugins_dir``, sorted by name; ``[]`` when the dir
    does not exist. Raises ``ViewPluginError`` naming the first bad plugin."""
    if not plugins_dir.is_dir():
        return []
    out: list[ViewPlugin] = []
    owner: dict[str, str] = {}
    for sub in sorted(plugins_dir.iterdir()):
        if not sub.is_dir() or is_furniture(sub.name):
            continue
        plugin = load_view_plugin(sub)
        for kind in plugin.manifest.kinds:
            if kind in owner:
                raise ViewPluginError(
                    f"view kind {kind!r} is claimed by two plugins, {owner[kind]!r} and "
                    f"{plugin.name!r} (in {plugins_dir}) — a view file could not say which "
                    "one it means"
                )
            owner[kind] = plugin.name
        out.append(plugin)
    logger.info("view plugins: discovered %d from %s", len(out), plugins_dir)
    return out
