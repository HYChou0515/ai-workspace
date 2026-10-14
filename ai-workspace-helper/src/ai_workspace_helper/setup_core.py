"""Installing the helper on the person's PC — the first version and every one
after it (docs/plan-outside-helper.md D16, D21, §3.7.1, §3.7.5).

STANDARD LIBRARY ONLY, no relative imports, Python 3.9+: this one file runs in
three places — inside the installed package (the window: its requests, the
Startup shortcut, uninstall), at the root of the zip beside `install.pyw`
(before anything is installed), and beside `launch.pyw` in the helper's folder.
It is copied, never edited: by the pack script into the zip, and by `install`
beside the launcher.

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
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

#: The shortcut's file name, on the desktop and in the Startup folder (D13).
SHORTCUT_NAME = "AI Workspace Helper"

Run = Callable[[list[str]], None]


class SetupFailed(Exception):
    """A step of installing or updating failed; the message says which."""


def root() -> Path:
    """`%LOCALAPPDATA%\\AIWorkspaceHelper\\`; `AI_WORKSPACE_HELPER_HOME` overrides it
    (tests, a developer's Linux box)."""
    override = os.environ.get("AI_WORKSPACE_HELPER_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "AIWorkspaceHelper"
    return Path.home() / ".local" / "share" / "ai-workspace-helper"


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


# ── one version (§3.7.5 step 1) ─────────────────────────────────────────────


#: What `install` copies from the zip into the helper's folder: the fixed
#: launcher and this file.
_CARRIED = ("launch.pyw", "setup_core.py")


def install_version(
    home: Path,
    base_python: str,
    wheelhouse: Path,
    version: str,
    *,
    run: Run = run_quietly,
    before_replacing: Callable[[], None] | None = None,
) -> Path:
    """Build `versions/<version>/`: a venv from `base_python`, the helper and
    its packages from `wheelhouse` only, then a self-test. It is built as
    `<version>.partial` and renamed only once all of that passed — a failed
    step leaves nothing behind for the launcher to pick up.
    `before_replacing` is asked right before an existing `versions/<version>/`
    is removed for the new build; raising stops it (D21: a helper may have
    started on it while this was building)."""
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
                f"ai-workspace-helper=={version}",
            ]
        )
        run([python, "-m", "ai_workspace_helper", "--self-test"])
        if final.exists():
            # reinstalling the same version (D16: run install.pyw again)
            if before_replacing is not None:
                before_replacing()
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
    folder: str,
    *,
    target: str,
    arguments: str,
    workdir: str,
    name: str = SHORTCUT_NAME,
    in_dir: str | None = None,
) -> list[str]:
    """Create `<folder>\\<name>.lnk` (`folder` is a .NET special folder:
    `Desktop`, `Startup`) — or in the folder `in_dir` names (the precheck's).
    Asked of Windows rather than built from the user profile, so a Desktop
    redirected to OneDrive still gets it. Any error fails the command:
    `powershell -Command` may otherwise carry on and exit 0 with no shortcut
    made (a constrained language mode refuses New-Object -ComObject)."""
    where = _ps(in_dir) if in_dir is not None else f"[Environment]::GetFolderPath({_ps(folder)})"
    script = "; ".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$d = {where}",
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


# ── starting (launch.pyw, §3.7.5) ───────────────────────────────────────────


def version_command(home: Path, version: str) -> list[str]:
    venv = home / "versions" / version / "venv"
    return [str(venv_python(venv, windowed=True)), "-m", "ai_workspace_helper"]


def launch_command(home: Path, state: dict[str, Any]) -> list[str]:
    """How the fixed launcher starts the helper: the version `state` names,
    in its own venv, with no console window."""
    current = state.get("current")
    if not current:
        raise SetupFailed("the helper is not installed — run install.pyw")
    return version_command(home, str(current))


#: Written by the launcher when a helper is already running: that helper sees
#: it and brings its window forward (§3.7.5 step 1).
SHOW_REQUEST = "show.request"
#: Written by install.pyw for a helper still running the version it replaced:
#: that helper sees it and ends, so the new one can start (D21, §3.7.5).
QUIT_REQUEST = "quit.request"


def helper_running(home: Path) -> bool:
    """Whether a helper holds `helper.lock` — asked by taking it for a moment."""
    lock = InstanceLock(home / "helper.lock")
    if lock.acquire():
        lock.release()
        return False
    return True


def launcher_home(here: Path) -> Path:
    """The helper's folder for a launch.pyw in `here`: its own folder once
    installed there — or, for the copy in the unzipped download (the zip
    carries one for `install` to copy), the installed helper's folder. With
    nothing installed it stays in `here`: launching takes a lock there, and
    after 解除安裝 the helper's folder must not come back (D17)."""
    if (here / "state.json").exists():
        return here
    installed = root()
    return installed if (installed / "state.json").exists() else here


def launch(
    home: Path,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    wait_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """What `launch.pyw` does: start the current version. Returns it, or
    `"shown"` when a helper was already running and has been asked to come
    forward. `wait_s`: `replace_running` starts this while the old helper is
    still ending — wait that long for its lock (which goes with its process)
    before deciding."""
    lock = InstanceLock(home / "helper.lock")
    waited = 0.0
    while not lock.acquire() and waited < wait_s:
        sleep(0.5)
        waited += 0.5
    if not lock.held:
        (home / SHOW_REQUEST).write_text("show", encoding="utf-8")
        return "shown"
    # holding the lock, no helper runs: a request left for one is stale and
    # must not end the helper about to start
    (home / QUIT_REQUEST).unlink(missing_ok=True)
    lock.release()  # the helper takes it for itself
    state = read_state(home)
    popen(launch_command(home, state), cwd=str(home))
    return str(state["current"])


def replace_running(
    home: Path,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    wait_s: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """install.pyw, a new version in place while a helper runs (D21, §3.7.5
    step 3): ask that helper to end, then start the current version once its
    lock is free. `"shown"` when it did not end within `wait_s`: it is left
    running and brought forward, and the request is withdrawn — it must not
    end later under the person's feet."""
    request = home / QUIT_REQUEST
    request.write_text("quit", encoding="utf-8")
    try:
        return launch(home, popen=popen, wait_s=wait_s, sleep=sleep)
    finally:
        request.unlink(missing_ok=True)


def take_over(home: Path, version: str) -> bool:
    """The way from 0.1.0 (D15) to D21. A 0.1.0 that reaches AI Workspace
    still downloads the next version itself and marks it `pending`, and its
    launcher starts it — counting starts, and rolling it back for good after
    three without the confirmation D15 expected. So `version`, once started
    as that pending one, takes over at once: current, the old one previous,
    D15's fields gone, every other version folder removed, and the launcher
    and this file it brought (D15 put them in its folder) moved to the root.
    False, changing nothing, when it was not started that way."""
    s = read_state(home)
    if s.get("pending") != version:
        return False
    old = s.get("current")
    kept = {k: v for k, v in s.items() if k not in _AUTOMATIC_UPDATE_FIELDS}
    if old == version:
        # install.pyw already made it current: the version before stays previous
        write_state(home, kept)
        return True
    write_state(home, {**kept, "current": version, "previous": old})
    for folder in (home / "versions").iterdir():
        if folder.name not in (version, old):
            shutil.rmtree(folder, ignore_errors=True)
    for name in _CARRIED:
        mine = home / "versions" / version / name
        if mine.is_file():
            # text files, and the launcher has exited by now: nothing locks them
            tmp = home / f".{name}.new"
            shutil.copy2(mine, tmp)
            os.replace(tmp, home / name)
    return True


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
UNINSTALL_LOG = "ai-workspace-helper-uninstall.log"


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
            "小幫手解除安裝時刪不掉，請手動刪除：\n" + "\n".join(left) + "\n",
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
            "AI Workspace Helper",
            "這些刪不掉（可能被防毒鎖住），請手動刪除：\n\n"
            + "\n".join(left)
            + f"\n\n清單也寫在 %TEMP%\\{UNINSTALL_LOG}",
        )
    here = Path(__file__).resolve()
    if here.parent.name.startswith("ai-workspace-helper-"):  # the window's mkdtemp
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
    wheels = sorted(wheelhouse.glob("ai_workspace_helper-*.whl"))
    if not wheels:
        raise SetupFailed(f"no ai_workspace_helper wheel in {wheelhouse}")
    return wheels[-1].name.split("-")[1]


#: What D15's automatic update kept in state.json; D21 writes none of it.
_AUTOMATIC_UPDATE_FIELDS = ("pending", "pending_attempts", "bad", "failures", "update")


class Installed(NamedTuple):
    version: str
    #: Why the desktop shortcut could not be made ("" when it was): everything
    #: else is in place, and the person is told how to start the helper.
    shortcut_error: str


def install(zip_dir: Path, home: Path, *, base_python: str, run: Run = run_quietly) -> Installed:
    """Everything `install.pyw` does once the person confirmed `base_python`:
    the version from the zip, the folder layout, `state.json`, the fixed
    launcher, and the desktop shortcut."""
    version = zip_version(zip_dir / "wheelhouse")
    # A running helper holds the lock — which version it runs, the lock does
    # not say: so no folder already there is touched under it (D21, §3.7.5)
    running = helper_running(home)
    in_use = SetupFailed(
        f"小幫手正在執行，{version} 已經裝著。請先在小幫手的視窗按「結束」，再重新安裝這一版。"
    )
    if running and (home / "versions" / version).exists():
        raise in_use

    def still_free() -> None:
        # building takes about a minute: a helper started meanwhile (a
        # shortcut) runs `current` — perhaps this very folder
        if helper_running(home):
            raise in_use

    install_version(
        home, base_python, zip_dir / "wheelhouse", version, run=run, before_replacing=still_free
    )
    old = read_state(home)
    current = old.get("current")
    previous = current if current and current != version else old.get("previous")
    write_state(
        home,
        {
            # what the person recorded stays (start_at_login: the Startup
            # shortcut is still there); what only the automatic update wrote
            # (D15) goes; install sets the rest
            **{k: v for k, v in old.items() if k not in _AUTOMATIC_UPDATE_FIELDS},
            "base_python": base_python,
            "current": version,
            "previous": previous,
        },
    )
    # D21 (§3.7.5 step 2): only current and previous stay — tidied when no
    # helper runs (with one running, the next install tidies); a folder that
    # cannot go now (held) goes on a later install
    if not running:
        for folder in (home / "versions").iterdir():
            if folder.name not in (version, previous):
                shutil.rmtree(folder, ignore_errors=True)
    for name in _CARRIED:
        shutil.copy2(zip_dir / name, home / name)
    try:
        run(_launcher_shortcut("Desktop", base_python, home))
    except SetupFailed as exc:
        return Installed(version, str(exc))
    return Installed(version, "")


# ── the precheck (plan D19): may this PC run the helper? ────────────────────


class Check(NamedTuple):
    name: str
    ok: bool
    #: What was found, or — when not ok — what stops it and whom to ask.
    detail: str


#: Where Playwright keeps the Node it drives Chrome with, inside its wheel.
_NODE = "playwright/driver/node.exe"


def chrome_install_paths() -> list[Path]:  # pragma: no cover — Windows folders
    """Where Chrome installs: for all users, or for this one."""
    found = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            found.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return found


#: The CPython versions the helper installs on (D14, D23): the zip carries
#: wheels for each, and `install.pyw` and the precheck accept exactly these —
#: 3.9 because some people's PCs have nothing newer.
PYTHONS = ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14")


def python_too_old(version: tuple[int, ...]) -> str | None:
    """Why this Python cannot run the helper, or None when it can — the one
    answer `install.pyw` and the precheck both give."""
    oldest = tuple(int(n) for n in PYTHONS[0].split("."))
    if tuple(version[:2]) >= oldest:
        return None
    return f"需要 Python {PYTHONS[0]} 以上，這是 {'.'.join(str(n) for n in version)}"


def chrome_policy(name: str) -> int | None:  # pragma: no cover — the Windows registry
    """A Chrome policy set by the company (machine first, then user), or None."""
    if sys.platform != "win32":
        return None
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Policies\Google\Chrome") as key:
                return int(winreg.QueryValueEx(key, name)[0])
        except OSError:
            continue
    return None


def precheck(
    zip_dir: Path,
    scratch: Path,
    *,
    base_python: str,
    python_version: tuple[int, int, int],
    chrome_paths: list[Path],
    chrome_policy: Callable[[str], int | None],
    run: Run = run_quietly,
) -> list[Check]:
    """What `precheck.pyw` checks, installing nothing: each thing the helper
    needs that a company PC may refuse a person without admin rights. Every
    check runs whatever the others found; `scratch` (beside where the helper
    would live, so the same rules apply) is removed at the end."""
    checks: list[Check] = []

    def check(name: str, step: Callable[[], str], hint: str) -> None:
        try:
            checks.append(Check(name, True, step()))
        except (SetupFailed, OSError, KeyError, zipfile.BadZipFile) as exc:
            checks.append(Check(name, False, f"{hint}（{exc}）"))

    version = ".".join(str(n) for n in python_version)

    def python_ok() -> str:
        if python_too_old(python_version):
            raise SetupFailed(f"這是 {version}")
        return f"{version}（{base_python}）"

    def runs_here() -> str:
        venv = scratch / "venv"
        run([base_python, "-m", "venv", "--without-pip", str(venv)])
        run([str(venv_python(venv)), "-c", "pass"])
        return "可以"

    def node_runs() -> str:
        wheels = sorted((zip_dir / "wheelhouse").glob("playwright-*.whl"))
        if not wheels:
            raise SetupFailed("zip 不完整：沒有 Playwright")
        node = scratch / "node.exe"
        with zipfile.ZipFile(wheels[-1]) as z:
            try:
                data = z.read(_NODE)
            except KeyError as exc:
                raise SetupFailed("zip 不完整：Playwright 裡沒有 node.exe") from exc
        node.write_bytes(data)
        run([str(node), "--version"])
        return "可以"

    def shortcut_ok() -> str:
        run(
            shortcut_command(
                "",
                target=base_python,
                arguments="",
                workdir=str(scratch),
                name="precheck",
                in_dir=str(scratch),
            )
        )
        if not (scratch / "precheck.lnk").exists():
            raise SetupFailed("PowerShell 沒有回報錯誤，但捷徑沒有建出來")
        return "可以"

    def chrome_found() -> str:
        for path in chrome_paths:
            if path.exists():
                return str(path)
        raise SetupFailed("沒有找到 Chrome")

    def automation_allowed() -> str:
        if chrome_policy("RemoteDebuggingAllowed") == 0:
            raise SetupFailed("公司的 Chrome 政策 RemoteDebuggingAllowed = 0")
        return "沒有被政策禁止"

    scratch.mkdir(parents=True, exist_ok=True)
    try:
        check(f"Python {PYTHONS[0]} 以上", python_ok, f"需要 Python {PYTHONS[0]}–{PYTHONS[-1]}")
        check(
            "在使用者資料夾執行程式",
            runs_here,
            "公司政策不讓程式在 %LOCALAPPDATA% 底下執行（例如 AppLocker）；"
            "請 IT 放行 %LOCALAPPDATA%\\AIWorkspaceHelper\\",
        )
        check(
            "Playwright 的 node.exe",
            node_runs,
            "防毒或公司政策擋下 Playwright 自帶的 node.exe；請 IT 放行它",
        )
        check(
            "建立桌面捷徑（PowerShell）",
            shortcut_ok,
            "PowerShell 被限制，建不了捷徑；安裝照樣能完成，之後從"
            " %LOCALAPPDATA%\\AIWorkspaceHelper\\launch.pyw 開",
        )
        check("Chrome", chrome_found, "沒有找到 Chrome；小幫手要用已安裝的 Chrome")
        check(
            "Chrome 允許自動化",
            automation_allowed,
            "Chrome 被政策禁止自動化，小幫手開不了它；請 IT 放行",
        )
    finally:
        _remove_tree(scratch)
    return checks


def precheck_summary(checks: list[Check]) -> str:
    """What `precheck.pyw` shows, and what 「複製結果」 copies."""
    lines = [f"{'✔' if c.ok else '✘'} {c.name}：{c.detail}" for c in checks]
    bad = sum(not c.ok for c in checks)
    if bad:
        lines.append(f"\n有 {bad} 項沒有通過。請把這份結果交給 IT 或系統管理員。")
    else:
        lines.append("\n這台電腦可以跑小幫手：接著雙擊 install.pyw。")
    return "\n".join(lines)
