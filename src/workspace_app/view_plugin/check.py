"""`view_plugin check` — an installed plugin, checked without booting the app
(#847/#848 P11).

The same strict rules boot applies (`load_view_plugin`, the skill rules), plus
two a boot cannot see, found in the built web half:

- a bundled copy of React. In the planning spike such a plugin threw
  `Cannot read properties of null (reading 'useState')` on render;
- a dev build — it imports `react/jsx-dev-runtime`, which the import map does
  not provide, so the import fails before the plugin registers anything.

It also refuses a web half that still reads `process.env` (Vite's library mode
leaves it in), and a python sandbox bundle not built with the isolated launcher.

The web checks are read off the built files' TEXT: markers only a bundled React or a dev
JSX transform put there (the React 19 internals export, the element symbol the
JSX runtime stamps, the dev runtime's specifier). A production build with those
packages external contains none of them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..view_plugins.discovery import ViewPlugin, ViewPluginError, is_furniture, load_view_plugin
from ..view_plugins.skills import plugin_skill_sources

#: text → why a built web half carrying it is refused, and what to change.
_BUNDLED_REACT = (
    "__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE",
    "__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED",
    "react.transitional.element",
)
_DEV_BUILD = ("react/jsx-dev-runtime",)
#: Vite's LIBRARY mode leaves `process.env.NODE_ENV` in (an app build replaces
#: it); a bundled library's dev checks then throw `process is not defined` in
#: the browser — which a node-based test never sees (#855's live check).
_PROCESS_ENV = ("process.env.NODE_ENV",)
#: What only the isolated launcher template sets (`tooling.prebuild`).
_ISOLATED_MARK = "PYTHONNOUSERSITE=1"


@dataclass
class Report:
    name: str
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _check_web(p: ViewPlugin, report: Report) -> None:
    for js in sorted(p.web_dir.rglob("*.js")):
        text = js.read_text("utf-8", "replace")
        rel = js.relative_to(p.dir)
        if any(m in text for m in _BUNDLED_REACT):
            report.errors.append(
                f"{rel} carries its own copy of React — mark react, react/jsx-runtime, "
                "react-dom and react-dom/client EXTERNAL in the plugin's vite.config.ts "
                "(build.rollupOptions.external); the SPA provides them"
            )
        if any(m in text for m in _PROCESS_ENV):
            report.errors.append(
                f"{rel} still reads process.env.NODE_ENV (the browser has no `process`) — add "
                'define: {"process.env.NODE_ENV": JSON.stringify("production")} to the '
                "plugin's vite.config.ts; library mode does not replace it"
            )
        if any(m in text for m in _DEV_BUILD):
            report.errors.append(
                f"{rel} is a development build (it imports react/jsx-dev-runtime) — build "
                'in production mode (`vite build`, `mode: "production"`)'
            )


def _check_sandbox(p: ViewPlugin, report: Report) -> None:
    half = p.manifest.sandbox
    if half is None or half.bundle is None:
        return
    bundle = p.dir / half.bundle
    if not bundle.is_dir():
        report.notes.append(
            f"{half.bundle}/ is not in this dir — fine under sandbox.kind http, where the "
            "bundle lives in sandbox-host's builtin/; under sandbox.kind local its commands "
            "fail per call"
        )
        return
    launch = bundle / "launch"
    if not launch.is_file() or not os.access(launch, os.X_OK):
        report.errors.append(f"{half.bundle}/launch is missing or not executable")
        return
    if (bundle / ".venv").is_dir() and _ISOLATED_MARK not in launch.read_text("utf-8", "replace"):
        report.errors.append(
            f"{half.bundle}/launch is not the isolated launcher, so a user's `pip install` "
            "would change what this plugin's commands import — add "
            '[tool.workspace-tool] launch = "isolated" to the bundle\'s pyproject.toml and rebuild'
        )
    commands_json = bundle / "commands.json"
    names = (
        {c.get("name") for c in json.loads(commands_json.read_text())}
        if commands_json.is_file()
        else set()
    )
    if half.validate and "validate" not in names:
        report.errors.append(
            f'plugin.json says "validate": true but {half.bundle}/commands.json has no '
            "`validate` command"
        )


def check_plugin(folder: Path) -> Report:
    report = Report(name=folder.name)
    try:
        p = load_view_plugin(folder)
        plugin_skill_sources([p])
    except ViewPluginError as e:
        report.errors.append(str(e))
        return report
    _check_web(p, report)
    _check_sandbox(p, report)
    return report


def check_dir(plugins_dir: Path, name: str | None) -> list[Report]:
    if name is not None:
        return [check_plugin(plugins_dir / name)]
    if not plugins_dir.is_dir():
        return []
    return [
        check_plugin(d)
        for d in sorted(plugins_dir.iterdir())
        if d.is_dir() and not is_furniture(d.name)
    ]
