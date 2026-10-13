"""Installing (and later updating) the helper on the person's PC
(docs/plan-outside-helper.md D16, §3.7.1, §3.7.4).

STANDARD LIBRARY ONLY, no relative imports, Python 3.10+: this one file runs in
three places — inside the installed package (the helper's own updates), at the
root of the zip beside `install.pyw` (before anything is installed), and beside
`launch.pyw` in the helper's folder. It is copied, never edited: by the pack
script into the zip, by `install` beside the launcher, and by `confirm` from a
newly confirmed version.

Every command goes through an injected `run`, so the layout it builds is tested
on Linux; the default runs it with no console window.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: The shortcut's file name, on the desktop and in the Startup folder (D13).
SHORTCUT_NAME = "AI Workspace"

Run = Callable[[list[str]], None]


class SetupFailed(Exception):
    """A step of installing or updating failed; the message says which."""


def root() -> Path:
    """`%LOCALAPPDATA%\\AIWorkspace\\`; `AI_WORKSPACE_HOME` overrides it
    (tests, a developer's Linux box)."""
    override = os.environ.get("AI_WORKSPACE_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "AIWorkspace"
    return Path.home() / ".local" / "share" / "ai-workspace"


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


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Through a temporary file + `os.replace`: whoever reads it next finds
    the old contents or the new, never half of either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_state(home: Path, state: dict[str, Any]) -> None:
    """The launcher reads this at every start; it must never find half of it."""
    write_json(home / "state.json", state)


# ── one helper at a time (§3.7.5 step 1) ────────────────────────────────────


class InstanceLock:
    """`helper.lock`: an OS lock on an open file, so it goes away with the
    process however that ends — no stale lock after a crash."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self._path, "a+b")  # noqa: SIM115 — held open for the process's life
        try:
            _lock(f)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    @property
    def held(self) -> bool:
        return self._file is not None

    def release(self) -> None:
        if self._file is not None:
            _unlock(self._file)
            self._file.close()
            self._file = None


if sys.platform == "win32":  # pragma: no cover — verified on Windows by hand (D14)
    import msvcrt

    def _lock(f: Any) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f: Any) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(f: Any) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f: Any) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ── one version (§3.7.4 steps 3–5) ──────────────────────────────────────────


#: What a version keeps of its zip besides the venv: its own launcher and this
#: file, put in place at the root only once that version has proven itself.
_CARRIED = ("launch.pyw", "setup_core.py")


def install_version(
    home: Path,
    base_python: str,
    wheelhouse: Path,
    version: str,
    *,
    run: Run = run_quietly,
    carried_from: Path | None = None,
) -> Path:
    """Build `versions/<version>/`: a venv from `base_python`, the helper and
    its packages from `wheelhouse` only, then a self-test. It is built as
    `<version>.partial` and renamed only once all of that passed — a failed
    step leaves nothing behind for the launcher to pick up. `carried_from`
    (the unpacked zip) gives the version its own `_CARRIED` files."""
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
                f"ai-workspace=={version}",
            ]
        )
        run([python, "-m", "ai_workspace", "--self-test"])
        for name in _CARRIED if carried_from else ():
            if (carried_from / name).is_file():  # ty: ignore[unsupported-operator]
                shutil.copy2(carried_from / name, partial / name)  # ty: ignore[unsupported-operator]
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


def remove_shortcut_command(folder: str, *, name: str = SHORTCUT_NAME) -> list[str]:
    """Remove `<folder>\\<name>.lnk`; nothing there is not an error."""
    script = "; ".join(
        [
            f"$d = [Environment]::GetFolderPath({_ps(folder)})",
            f"Remove-Item -LiteralPath (Join-Path $d {_ps(name + '.lnk')})"
            " -ErrorAction SilentlyContinue",
        ]
    )
    return ["powershell", "-NoProfile", "-Command", script]


def _launcher_shortcut(folder: str, base_python: str, home: Path) -> list[str]:
    """Every shortcut is the same: the confirmed Python on the fixed launcher,
    never a version folder (§3.7 table: a start-up shortcut pointing at an old
    path)."""
    return shortcut_command(
        folder, target=base_python, arguments=f'"{home / "launch.pyw"}"', workdir=str(home)
    )


def startup_command(enabled: bool, *, base_python: str, home: Path) -> list[str]:
    """The window's 「開機時自動啟動」 (D13): the launcher's shortcut in the
    Windows Startup folder, or not."""
    if enabled:
        return _launcher_shortcut("Startup", base_python, home)
    return remove_shortcut_command("Startup")


# ── versions ────────────────────────────────────────────────────────────────


def version_key(text: str) -> tuple[int, ...]:
    """`1.10.0` as numbers — newer than `1.9.0`; unreadable reads as oldest."""
    try:
        return tuple(int(part) for part in str(text).strip().split("."))
    except ValueError:
        return (0,)


#: Starts a waiting version may take to confirm itself before it is given up.
MAX_PENDING_ATTEMPTS = 3
#: Failed preparations of one release before it is marked bad (§3.7.4).
MAX_PREPARE_FAILURES = 3


# ── starting (launch.pyw, §3.7.5) ───────────────────────────────────────────


def version_command(home: Path, version: str) -> list[str]:
    venv = home / "versions" / version / "venv"
    return [str(venv_python(venv, windowed=True)), "-m", "ai_workspace"]


def launch_command(home: Path, state: dict[str, Any]) -> list[str]:
    """How the fixed launcher starts the helper: the version `state` names,
    in its own venv, with no console window."""
    current = state.get("current")
    if not current:
        raise SetupFailed("the helper is not installed — run install.pyw")
    return version_command(home, str(current))


def start_choice(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Which version this start runs, and the state to write BEFORE starting
    it. A waiting version is tried, each try counted; one that has had
    `MAX_PENDING_ATTEMPTS` starts and never confirmed itself (it crashes on
    start) is marked bad and the current one runs again — said once, on the
    next check-in."""
    s = dict(state)
    pending = s.get("pending")
    if not pending:
        return str(s.get("current")), s
    attempts = int(s.get("pending_attempts") or 0)
    if attempts >= MAX_PENDING_ATTEMPTS:
        s["bad"] = [*(v for v in s.get("bad", []) if v != pending), pending]
        s["pending"], s["pending_attempts"] = None, 0
        s["update"] = f"{pending} 起不來,已退回 {s.get('current')}"
        return str(s.get("current")), s
    s["pending_attempts"] = attempts + 1
    return str(pending), s


#: Written by the launcher when a helper is already running: that helper sees
#: it and brings its window forward (§3.7.5 step 1).
SHOW_REQUEST = "show.request"


def launch(
    home: Path,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    wait_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """What `launch.pyw` does. Returns the version started, or `"shown"` when a
    helper was already running and has been asked to come forward. `wait_s`:
    「重新開啟」 starts this while the old helper is still ending — wait that
    long for its lock (which goes with its process) before deciding."""
    lock = InstanceLock(home / "helper.lock")
    waited = 0.0
    while not lock.acquire() and waited < wait_s:
        sleep(0.5)
        waited += 0.5
    if not lock.held:
        (home / SHOW_REQUEST).write_text("show", encoding="utf-8")
        return "shown"
    lock.release()  # the helper takes it for itself
    state = read_state(home)
    if not state.get("current"):
        raise SetupFailed("the helper is not installed — run install.pyw")
    version, state = start_choice(state)
    write_state(home, state)  # before starting: a crash on start still counts
    popen(version_command(home, version), cwd=str(home))
    return version


def confirm(home: Path, version: str) -> bool:
    """The waiting `version` is up and connected: it becomes current, the one
    before it previous, every other version folder goes, and its own launcher
    replaces the fixed one — only now, when it has proven itself (§3.7.5 steps
    3 and 6). False, changing nothing, when `version` is not the one waiting."""
    s = read_state(home)
    if s.get("pending") != version:
        return False
    old = s.get("current")
    s.update(current=version, previous=old, pending=None, pending_attempts=0)
    s["update"] = f"已更新到 {version}"
    write_state(home, s)
    for folder in (home / "versions").iterdir():
        if folder.name not in (version, old):
            shutil.rmtree(folder, ignore_errors=True)
    for name in _CARRIED:
        mine = home / "versions" / version / name
        if mine.is_file() and (
            not (home / name).is_file() or mine.read_bytes() != (home / name).read_bytes()
        ):
            # .pyw is text and the launcher has exited by now: nothing locks it
            tmp = home / f".{name}.new"
            shutil.copy2(mine, tmp)
            os.replace(tmp, home / name)
    return True


# ── the next version, in the background (§3.7.4) ────────────────────────────


def _plain_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not any(c in name for c in "/\\:")
        and name not in (".", "..")
    )


def _unpack(data: bytes, into: Path) -> None:
    import io
    import zipfile

    shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.namelist():
            target = (into / member).resolve()
            if into.resolve() not in target.parents:
                raise SetupFailed(f"the update zip names a path outside itself: {member!r}")
        z.extractall(into)


def update_outcome(home: Path, state: dict[str, Any], manifest: dict[str, Any]) -> str | None:
    """What `prepare_update` would answer without fetching anything — or None
    when the release has to be fetched and installed."""
    version = str(manifest.get("version", ""))
    if version_key(version) <= version_key(str(state.get("current", "0"))):
        return "current"
    if version in state.get("bad", []):
        return "bad"
    if state.get("pending") == version and (home / "versions" / version).is_dir():
        return "ready"
    return None


def prepare_update(
    home: Path,
    manifest: dict[str, Any],
    fetch: Callable[[str], bytes],
    *,
    run: Run = run_quietly,
) -> str:
    """Make the published release ready for the next start, never touching the
    running one. `"current"`: nothing newer. `"bad"`: it failed before and is
    not tried again. `"ready"`: installed, waiting for the next start. Raises
    `SetupFailed` when this try failed (the running version carries on); the
    same release failing `MAX_PREPARE_FAILURES` times is marked bad."""
    import hashlib

    state = read_state(home)
    settled = update_outcome(home, state, manifest)
    if settled is not None:
        return settled
    version = str(manifest.get("version", ""))
    name = manifest.get("file")
    if not _plain_name(name):
        raise SetupFailed(f"the release names a file that is not a plain name: {name!r}")
    downloads = home / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    unpacked = downloads / version
    try:
        data = fetch(str(name))
        if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            raise SetupFailed(f"{name}: sha256 does not match the release — not installed")
        _unpack(data, unpacked)
        install_version(
            home,
            str(state["base_python"]),
            unpacked / "wheelhouse",
            version,
            run=run,
            carried_from=unpacked,
        )
    except BaseException:
        s = read_state(home)
        failures = dict(s.get("failures") or {})
        failures[version] = int(failures.get(version, 0)) + 1
        if failures[version] >= MAX_PREPARE_FAILURES:
            s["bad"] = [*(v for v in s.get("bad", []) if v != version), version]
            failures.pop(version)
        s["failures"] = failures
        write_state(home, s)
        raise
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)
    s = read_state(home)
    s["pending"], s["pending_attempts"] = version, 0
    (s.get("failures") or {}).pop(version, None)
    write_state(home, s)
    return "ready"


# ── uninstall (D17) ─────────────────────────────────────────────────────────


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def uninstall(
    home: Path, *, run: Run = run_quietly, remove_tree: Callable[[Path], None] = _remove_tree
) -> list[str]:
    """Remove both shortcuts and the helper's folder. Returns what could not be
    removed (an antivirus holding a file), for the person to delete by hand."""
    left: list[str] = []
    for folder in ("Desktop", "Startup"):
        try:
            run(remove_shortcut_command(folder))
        except SetupFailed:
            left.append(f"{folder}\\{SHORTCUT_NAME}.lnk")
    with contextlib.suppress(OSError):  # what is left is listed below
        remove_tree(home)
    if home.exists():
        left.append(str(home))
    return left


#: Where the clean-up process writes what it could not remove.
UNINSTALL_LOG = "ai-workspace-uninstall.log"


def uninstall_main(
    home: Path,
    *,
    run: Run = run_quietly,
    sleep: Callable[[float], None] = time.sleep,
    log_dir: Path | None = None,
    remove_tree: Callable[[Path], None] = _remove_tree,
    wait_s: float = 60.0,
) -> list[str]:
    """The clean-up process the window's 「解除安裝」 starts (D17), from a copy
    of this file in %TEMP% — the helper's own files cannot be removed while it
    runs. It waits for the helper to end (its lock comes free), removes
    everything, and writes down what it could not."""
    lock = InstanceLock(home / "helper.lock")
    waited = 0.0
    while not lock.acquire() and waited < wait_s:
        sleep(0.5)
        waited += 0.5
    lock.release()  # an open lock file cannot be removed on Windows
    left = uninstall(home, run=run, remove_tree=remove_tree)
    if left:
        log = (log_dir or Path(tempfile.gettempdir())) / UNINSTALL_LOG
        log.write_text(
            "小幫手解除安裝時刪不掉,請手動刪除:\n" + "\n".join(left) + "\n",
            encoding="utf-8",
        )
    return left


def _uninstall_cli(home: str) -> int:  # pragma: no cover — the %TEMP% process; verified on Windows
    left = uninstall_main(Path(home))
    if left:
        import tkinter
        from tkinter import messagebox

        tkinter.Tk().withdraw()
        messagebox.showwarning(
            "AI Workspace",
            "這些刪不掉(可能被防毒鎖住),請手動刪除:\n\n"
            + "\n".join(left)
            + f"\n\n清單也寫在 %TEMP%\\{UNINSTALL_LOG}",
        )
    here = Path(__file__).resolve()
    if here.parent.name.startswith("ai-workspace-"):  # the window's mkdtemp
        shutil.rmtree(here.parent, ignore_errors=True)
    else:
        here.unlink(missing_ok=True)  # this copy, in %TEMP%
    return 0


if __name__ == "__main__":  # pragma: no cover — `python setup_core.py --uninstall <home>`
    if len(sys.argv) == 3 and sys.argv[1] == "--uninstall":
        sys.exit(_uninstall_cli(sys.argv[2]))
    sys.exit(2)


# ── first install (D16) ─────────────────────────────────────────────────────


def zip_version(wheelhouse: Path) -> str:
    """The helper version a zip carries — its own wheel's name says it."""
    wheels = sorted(wheelhouse.glob("ai_workspace-*.whl"))
    if not wheels:
        raise SetupFailed(f"no ai_workspace wheel in {wheelhouse}")
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
            # what was recorded stays (start_at_login: the Startup shortcut is
            # still there; the last update's outcome) — install sets the rest
            **old,
            "base_python": base_python,
            "current": version,
            "previous": current if current and current != version else old.get("previous"),
            "pending": None,
            "pending_attempts": 0,
            "bad": [v for v in old.get("bad", []) if v != version],
        },
    )
    for name in _CARRIED:
        shutil.copy2(zip_dir / name, home / name)
    run(_launcher_shortcut("Desktop", base_python, home))
    return version
