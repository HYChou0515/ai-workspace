"""The view-plugin CLI (#847/#848) — ``python -m workspace_app.view_plugin``.

* ``build <view-plugins/NAME> <DEST_ROOT>`` (or ``build --all <SRC_ROOT> <DEST_ROOT>``)
  — install a plugin from its source folder into a plugin dir: the web half
  through ``view-plugins/build-web.mjs`` (the one script the image's
  ``view-plugins`` stage runs too), and a ``sandbox-src/`` uv project prebuilt
  into ``<DEST>/<NAME>/sandbox``.

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
    print(f"✓ {name} → {dest_root / name}")


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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m workspace_app.view_plugin")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="install plugin source folder(s) into a plugin dir")
    b.add_argument("--all", action="store_true", help="SRC is a folder of plugin folders")
    b.add_argument("src")
    b.add_argument("dest")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _cmd_build(args)
