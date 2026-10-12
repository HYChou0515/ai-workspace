"""Installing (and later updating) the helper on the person's PC
(docs/plan-outside-helper.md D16, §3.7.1, §3.7.4).

STANDARD LIBRARY ONLY, no relative imports, Python 3.10+: this one file runs in
three places — inside the installed package (the helper's own updates), at the
root of the zip beside `install.pyw` (before anything is installed), and beside
`launch.pyw` in the helper's folder. The pack script copies it verbatim to the
other two; it is never edited there.

Every command goes through an injected `run`, so the layout it builds is tested
on Linux; the default runs it with no console window.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: The shortcut's file name, on the desktop and in the Startup folder (D13).
SHORTCUT_NAME = "Outside Helper"

Run = Callable[[list[str]], None]


class SetupFailed(Exception):
    """A step of installing or updating failed; the message says which."""


def root() -> Path:
    """`%LOCALAPPDATA%\\OutsideHelper\\`; `OUTSIDE_HELPER_HOME` overrides it
    (tests, a developer's Linux box)."""
    override = os.environ.get("OUTSIDE_HELPER_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "OutsideHelper"
    return Path.home() / ".local" / "share" / "outside-helper"


def venv_python(venv: Path, *, windowed: bool = False) -> Path:
    """The venv's interpreter; `windowed` is `pythonw.exe` (no console)."""
    if sys.platform == "win32":
        return venv / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return venv / "bin" / "python"


def run_quietly(
    argv: list[str],
) -> None:  # pragma: no cover — real processes; `run` is injected in tests
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(argv, check=True, capture_output=True, text=True, creationflags=flags)
    except (OSError, subprocess.CalledProcessError) as exc:
        tail = (getattr(exc, "stderr", "") or str(exc)).strip().splitlines()[-3:]
        raise SetupFailed(f"{Path(argv[0]).name} {' '.join(argv[1:3])}: {' '.join(tail)}") from exc


# ── state.json (§3.7.1) ─────────────────────────────────────────────────────


def read_state(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(home: Path, state: dict[str, Any]) -> None:
    """Through a temporary file + `os.replace`: the launcher reads this at
    every start, and must never find half of it."""
    home.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=home, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, home / "state.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── one version (§3.7.4 steps 3–5) ──────────────────────────────────────────


def install_version(
    home: Path, base_python: str, wheelhouse: Path, version: str, *, run: Run = run_quietly
) -> Path:
    """Build `versions/<version>/`: a venv from `base_python`, the helper and
    its packages from `wheelhouse` only, then a self-test. It is built as
    `<version>.partial` and renamed only once all of that passed — a failed
    step leaves nothing behind for the launcher to pick up."""
    versions = home / "versions"
    partial, final = versions / f"{version}.partial", versions / version
    versions.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(partial, ignore_errors=True)
    try:
        run([base_python, "-m", "venv", str(partial / "venv")])
        python = str(venv_python(partial / "venv"))
        run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                f"outside-helper=={version}",
            ]
        )
        run([python, "-m", "outside_helper", "--self-test"])
        if final.exists():
            # reinstalling the same version (D16: run install.pyw again)
            shutil.rmtree(final)
        os.replace(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return final


# ── shortcuts (D13, D16) ────────────────────────────────────────────────────


def _ps(text: str) -> str:
    """A PowerShell single-quoted literal: nothing inside is interpreted."""
    return "'" + text.replace("'", "''") + "'"


def shortcut_command(
    folder: str, *, target: str, arguments: str, workdir: str, name: str = SHORTCUT_NAME
) -> list[str]:
    """Create `<folder>\\<name>.lnk` (`folder` is a .NET special folder:
    `Desktop`, `Startup`). Asked of Windows rather than built from the user
    profile, so a Desktop redirected to OneDrive still gets it."""
    script = "; ".join(
        [
            f"$d = [Environment]::GetFolderPath({_ps(folder)})",
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
            f"(Join-Path $d {_ps(name + '.lnk')}))",
            f"$s.TargetPath = {_ps(target)}",
            f"$s.Arguments = {_ps(arguments)}",
            f"$s.WorkingDirectory = {_ps(workdir)}",
            "$s.Save()",
        ]
    )
    return ["powershell", "-NoProfile", "-Command", script]


# ── starting (launch.pyw, §3.7.5) ───────────────────────────────────────────


def launch_command(home: Path, state: dict[str, Any]) -> list[str]:
    """How the fixed launcher starts the helper: the version `state` names,
    in its own venv, with no console window."""
    current = state.get("current")
    if not current:
        raise SetupFailed("the outside helper is not installed — run install.pyw")
    venv = home / "versions" / str(current) / "venv"
    return [str(venv_python(venv, windowed=True)), "-m", "outside_helper"]


# ── first install (D16) ─────────────────────────────────────────────────────


def zip_version(wheelhouse: Path) -> str:
    """The helper version a zip carries — its own wheel's name says it."""
    wheels = sorted(wheelhouse.glob("outside_helper-*.whl"))
    if not wheels:
        raise SetupFailed(f"no outside_helper wheel in {wheelhouse}")
    return wheels[-1].name.split("-")[1]


def install(zip_dir: Path, home: Path, *, base_python: str, run: Run = run_quietly) -> str:
    """Everything `install.pyw` does once the person confirmed `base_python`:
    the version from the zip, the folder layout, `state.json`, the fixed
    launcher, and the desktop shortcut. Returns the version installed."""
    version = zip_version(zip_dir / "wheelhouse")
    install_version(home, base_python, zip_dir / "wheelhouse", version, run=run)
    old = read_state(home)
    current = old.get("current")
    write_state(
        home,
        {
            "base_python": base_python,
            "current": version,
            "previous": current if current and current != version else old.get("previous"),
            "pending": None,
            "pending_attempts": 0,
            "bad": [v for v in old.get("bad", []) if v != version],
        },
    )
    for name in ("launch.pyw", "setup_core.py"):
        shutil.copy2(zip_dir / name, home / name)
    run(
        shortcut_command(
            "Desktop",
            target=base_python,
            arguments=f'"{home / "launch.pyw"}"',
            workdir=str(home),
        )
    )
    return version
