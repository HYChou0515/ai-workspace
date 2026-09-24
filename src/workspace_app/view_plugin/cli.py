"""The view-plugin CLI (#847/#848) — ``python -m workspace_app.view_plugin``.

* ``new <name> [--with-sandbox] [--with-skill]`` — scaffold a buildable plugin
  under ``view-plugins/`` (``scaffold.py``).
* ``build <view-plugins/NAME> <DEST_ROOT>`` (or ``build --all <SRC_ROOT> <DEST_ROOT>``)
  — install a plugin from its source folder into a plugin dir: the web half
  through ``view-plugins/build-web.mjs`` (the one script the image's
  ``view-plugins`` stage runs too), and a ``sandbox-src/`` uv project prebuilt
  into ``<DEST>/<NAME>/sandbox``. Then ``check``s what it installed.
* ``check [name]`` — check the INSTALLED plugins (``view_plugins.dir``) without
  booting the app (``check.py``).
* ``tune <name>`` — the operator's one-command retune: ``skill_eval`` on the
  installed ``<dir>/<name>/skill/SKILL.md`` with ``<dir>/<name>/scenarios/`` and
  ``--control``. Edit that file, rerun, done.

``main(argv)`` returns the process exit code; ``__main__`` wraps it in
``SystemExit``. Kept importable so tests drive it directly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tomllib
from pathlib import Path

from ..tooling.prebuild import build_package
from ..view_plugins.discovery import ViewPluginError, discover_view_plugins, resolve_plugins_dir
from .check import Report, check_dir, check_plugin
from .scaffold import scaffold_plugin

_REPO = Path(__file__).resolve().parents[3]
BUILD_WEB = _REPO / "view-plugins" / "build-web.mjs"
#: The installed name of a plugin's prebuilt sandbox half — what its
#: `plugin.json` `sandbox.bundle` must say when it ships a `sandbox-src/`.
SANDBOX_BUNDLE = "sandbox"


class BuildError(RuntimeError):
    pass


def _script_name(sandbox_src: Path) -> str:
    """The bundle's one console script — the name its `launch` dispatches to."""
    scripts = tomllib.loads((sandbox_src / "pyproject.toml").read_text()).get("project", {})
    names = list(scripts.get("scripts", {}))
    if len(names) != 1:
        raise BuildError(
            f"{sandbox_src}/pyproject.toml must declare exactly one [project.scripts] "
            f"entry (the command `launch` runs), found {names}"
        )
    return names[0]


def build_one(src: Path, dest_root: Path) -> None:
    manifest = json.loads((src / "plugin.json").read_text())
    name = manifest["name"]
    sandbox_src = src / "sandbox-src"
    if sandbox_src.is_dir():
        declared = (manifest.get("sandbox") or {}).get("bundle")
        if declared != SANDBOX_BUNDLE:
            raise BuildError(
                f"{src}/plugin.json: a plugin with a sandbox-src/ installs it as "
                f'`{SANDBOX_BUNDLE}/`, so it must say "sandbox": {{"bundle": "{SANDBOX_BUNDLE}"}}'
            )
        launch = tomllib.loads((sandbox_src / "pyproject.toml").read_text())
        mode = launch.get("tool", {}).get("workspace-tool", {}).get("launch")
        if mode != "isolated":
            raise BuildError(
                f"{sandbox_src}/pyproject.toml: a view plugin's sandbox commands are the "
                "platform's, so a user's `pip install` must not change what they import — "
                'add [tool.workspace-tool] launch = "isolated"'
            )
    subprocess.run(["node", str(BUILD_WEB), str(src), str(dest_root)], check=True)
    if sandbox_src.is_dir():
        # Forced: an explicit build trusts no stamp. `_should_rebuild` hashes
        # only `sandbox-src/`, so a path dependency outside it (a vendored
        # platform package) would otherwise leave a stale bundle — #64's class.
        build_package(
            name=_script_name(sandbox_src),
            source=sandbox_src,
            dst=dest_root / name / SANDBOX_BUNDLE,
            force=True,
        )
    report = check_plugin(dest_root / name)
    _print(report)
    if report.errors:
        raise BuildError(f"{name}: the installed plugin does not pass `check`")


def _cmd_build(args: argparse.Namespace) -> int:
    src, dest = Path(args.src), Path(args.dest)
    sources = (
        [d for d in sorted(src.iterdir()) if (d / "plugin.json").is_file()] if args.all else [src]
    )
    try:
        for s in sources:
            build_one(s, dest)
    except (BuildError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


def _print(r: Report) -> None:
    print(f"{'✗' if r.errors else '✓'} {r.name}")
    for e in r.errors:
        print(f"    error: {e}")
    for n in r.notes:
        print(f"    note: {n}")


def _plugins_dir(config: str | None) -> Path:
    from ..config.loader import load

    return resolve_plugins_dir(load(config_path=Path(config) if config else None).view_plugins)


def _cmd_check(args: argparse.Namespace) -> int:
    root = _plugins_dir(args.config)
    reports = check_dir(root, args.name)
    if not reports:
        print(f"no view plugins in {root}")
        return 0
    for r in reports:
        _print(r)
    failed = any(r.errors for r in reports)
    if args.name is None and not failed:
        try:  # what only the whole set can show: a kind claimed twice
            discover_view_plugins(root)
        except ViewPluginError as e:
            print(f"error: {e}")
            return 1
    return 1 if failed else 0


def _cmd_new(args: argparse.Namespace) -> int:
    try:
        paths = scaffold_plugin(
            Path(args.root), args.name, with_sandbox=args.with_sandbox, with_skill=args.with_skill
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"created {len(paths)} file(s) under {Path(args.root) / args.name}:")
    for p in paths:
        print(f"  {p}")
    print(
        f"\nnext: `uv run python -m workspace_app.view_plugin build {Path(args.root) / args.name} "
        ".view-plugins` (or `make view-plugins`), then restart the app."
    )
    return 0


def _cmd_tune(args: argparse.Namespace) -> int:
    root = _plugins_dir(args.config)
    folder = root / args.name
    if not (folder / "plugin.json").is_file():
        print(f"error: no view plugin {args.name!r} in {root}")
        return 1
    manifest = json.loads((folder / "plugin.json").read_text())
    skill = manifest.get("skill")
    if not skill or not (folder / skill / "SKILL.md").is_file():
        print(f"error: view plugin {args.name!r} ships no skill (plugin.json `skill`) to tune")
        return 1
    scenarios = folder / "scenarios"
    if not scenarios.is_dir() or not any(scenarios.glob("*.json")):
        print(
            f"error: view plugin {args.name!r} has no scenarios/*.json in {folder} — "
            "tune scores the skill against them"
        )
        return 1
    from ..skill_eval.__main__ import main as skill_eval

    argv = [
        "--skill", str(folder / skill / "SKILL.md"),
        "--scenarios", str(scenarios),
        "--control",
        "--app", args.app,
        "--profile", args.profile,
        "-o", str(Path(args.out_dir) / args.name),
    ]  # fmt: skip
    if args.preset:
        argv += ["--preset", args.preset]
    if args.config:
        argv += ["--config", args.config]
    if args.num_ctx:
        argv += ["--num-ctx", str(args.num_ctx)]
    print(f"tuning {folder / skill / 'SKILL.md'} — edit that file and rerun to retune")
    try:
        skill_eval(argv)
    except SystemExit as exc:  # skill_eval exits 1 when a scenario failed
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m workspace_app.view_plugin")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="install plugin source folder(s) into a plugin dir")
    b.add_argument("--all", action="store_true", help="SRC is a folder of plugin folders")
    b.add_argument("src")
    b.add_argument("dest")
    n = sub.add_parser("new", help="scaffold a plugin under view-plugins/")
    n.add_argument("name")
    n.add_argument("--with-sandbox", action="store_true", help="add a sandbox-src/ half")
    n.add_argument("--with-skill", action="store_true", help="add skill/ and scenarios/")
    n.add_argument("--root", default=str(_REPO / "view-plugins"), help=argparse.SUPPRESS)
    c = sub.add_parser("check", help="check the installed plugins without booting the app")
    c.add_argument("name", nargs="?")
    c.add_argument("--config", default=None, help="config.yaml (for view_plugins.dir)")
    t = sub.add_parser("tune", help="score a plugin's installed skill on its scenarios")
    t.add_argument("name")
    t.add_argument("--preset", default=None)
    t.add_argument("--app", default="rca")
    t.add_argument("--profile", default="default")
    t.add_argument("--config", default=None, help="config.yaml (view_plugins.dir, presets)")
    t.add_argument("-o", "--out-dir", default="./view-plugin-tune")
    t.add_argument(
        "--num-ctx",
        type=int,
        default=0,
        help="ollama context window (skill_eval --num-ctx); a truncated prompt scores the window",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handler = {"build": _cmd_build, "new": _cmd_new, "check": _cmd_check, "tune": _cmd_tune}
    return handler[args.cmd](args)
