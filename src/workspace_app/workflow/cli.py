"""The workflow authoring CLI (#287) — ``python -m workspace_app.workflow``.

Two subcommands, the developer-facing half of the authoring DX:

* ``check [slug]`` — statically check every App's workflow profiles (or just one
  slug) and print the diagnostics. Exits non-zero if any *error* is found, so it
  doubles as a pre-startup / pre-commit gate (warnings are advisory).
* ``new <slug> <profile> <id> [--recipe ...] [--force]`` — scaffold a runnable
  workflow from a recipe (see ``scaffold.py``).

``main(argv)`` returns the process exit code; ``__main__`` wraps it in
``SystemExit``. Kept as an importable function so tests drive it directly.
"""

from __future__ import annotations

import argparse
from importlib import resources
from pathlib import Path

from ..apps.catalog import discover_app_slugs
from .authoring import check_app
from .scaffold import RECIPES, scaffold_workflow


def _apps_dir() -> Path:
    """The bundled ``apps/`` source dir the scaffold writes into (editable install)."""
    return Path(str(resources.files("workspace_app.apps")))


def _cmd_check(slug: str | None) -> int:
    slugs = [slug] if slug else discover_app_slugs()
    diags = [d for s in slugs for d in check_app(s)]
    for d in diags:
        print(d.render())
    errors = sum(1 for d in diags if d.level == "error")
    if not diags:
        print(f"✓ workflows look good ({len(slugs)} app(s) checked)")
    else:
        print(f"\n{errors} error(s), {len(diags) - errors} warning(s)")
    return 1 if errors else 0


def _cmd_new(args: argparse.Namespace) -> int:
    try:
        paths = scaffold_workflow(
            _apps_dir(),
            args.slug,
            args.profile,
            args.workflow_id,
            recipe=args.recipe,
            force=args.force,
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(f"created {len(paths)} file(s):")
    for p in paths:
        print(f"  {p}")
    print(
        "next: `python -m workspace_app.workflow check` to verify, then restart the app "
        "to pick it up."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workspace_app.workflow",
        description="Author and check workflows (#287).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="statically check workflow profiles for problems")
    check.add_argument("slug", nargs="?", help="app slug to check; omit to check every app")

    new = sub.add_parser("new", help="scaffold a runnable workflow from a recipe")
    new.add_argument("slug")
    new.add_argument("profile")
    new.add_argument("workflow_id")
    new.add_argument(
        "--recipe",
        choices=sorted(RECIPES),
        default="minimal",
        help="which starter shape to generate (default: minimal)",
    )
    new.add_argument(
        "--force", action="store_true", help="overwrite an existing workflow of the same id"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args.slug)
    return _cmd_new(args)
