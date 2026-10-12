"""The helper's window (docs/plan-outside-helper.md D13) and the thread behind it.

The window (tkinter, in the standard library) shows a status line and four
buttons — 登入, 暫停/繼續, 結束 — and draws nothing it did not read from
`Controller.snapshot()`. The controller lives on ONE worker thread: it owns the
Chrome (Playwright's sync API must stay on the thread that started it) and the
`Worker` loop, and takes the buttons as commands through a queue. Closing the
window ends the helper.
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
import threading
from collections.abc import Callable
from typing import Any

#: Seconds between polls when there is nothing to do. Short, because the card's
#: 「查」 waits on it; a poll is one small request.
POLL_S = 2.0

_TEXT = {
    "starting": "啟動中…",
    "connected": "已連線",
    "paused": "已暫停 — 卡片會改成自己查",
    "needs_login": "登入過期 — 請在小幫手的 Chrome 視窗登入",
    "update_required": "這個版本已停用,需要更新",
    "unreachable": "連不到 AI 工作區",
    "error": "發生錯誤",
    "no_chrome": "開不了 Chrome",
}

logger = logging.getLogger(__name__)


def describe(status: str, detail: str) -> str:
    """The status line the person reads."""
    text = _TEXT.get(status, status)
    if not detail:
        return text
    if status == "connected":
        return f"{text} — 正在查:{detail}"
    return f"{text}:{detail}"


def describe_update(status: str, *, forced: bool) -> str:
    """The window's line about the next version; `forced` when this version is
    refused (§3.7.6)."""
    if status == "preparing":
        return "正在準備新版…"
    if status == "ready":
        return "新版已備好,按「重新開啟」換上" if forced else "新版已備好,下次開啟生效"
    if status.startswith("failed"):
        why = status.partition(":")[2].strip()
        if forced:
            return f"新版準備失敗:{why}。請到 AI 工作區的「外網小幫手」頁重新下載、執行 install.pyw"
        return f"新版準備失敗:{why}(這一版照常使用)"
    return ""


class Controller:
    def __init__(
        self,
        make_chrome: Callable[[], Any],
        make_worker: Callable[[Any], Any],
        *,
        make_updater: Callable[[Any], Any] | None = None,
        poll_s: float = POLL_S,
    ) -> None:
        self._make_chrome = make_chrome
        self._make_worker = make_worker
        self._make_updater = make_updater
        self._poll_s = poll_s
        self._commands: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._snapshot = ("starting", "")
        self._chrome: Any = None
        self._worker: Any = None
        self._updater: Any = None
        self._asked_login = False
        #: Set by 「重新開啟」: the window starts the launcher once this ends.
        self.restart = False

    # ── the window's side (any thread) ──────────────────────────────────────

    def send(self, command: str) -> None:
        self._commands.put(command)

    def snapshot(self) -> tuple[str, str]:
        with self._lock:
            return self._snapshot

    def update_status(self) -> str:
        """The updater's: "" | "preparing" | "ready" | "failed: …"."""
        return str(getattr(self._updater, "status", "") or "")

    # ── the worker thread ───────────────────────────────────────────────────

    def open(self) -> None:
        try:
            self._chrome = self._make_chrome()
        except Exception as exc:  # noqa: BLE001 — shown to the person, who can only quit
            logger.exception("could not start Chrome")
            self._show("no_chrome", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
            return
        self._worker = self._make_worker(self._chrome)
        if self._make_updater is not None:
            self._updater = self._make_updater(self._chrome)

    def step(self, wait: float = 0.0) -> bool:
        """Take the commands waiting (up to `wait` seconds for the first), then
        poll once. False once the helper should end."""
        try:
            command: str | None = (
                self._commands.get(timeout=wait) if wait else self._commands.get_nowait()
            )
        except queue.Empty:
            command = None
        while command is not None:
            if command in ("quit", "restart"):
                self.restart = command == "restart"
                self._close()
                return False
            self._apply(command)
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                command = None
        if self._worker is None:
            return True  # Chrome never started: nothing to poll
        self._worker.tick()
        status = self._worker.status
        if status == "needs_login" and not self._asked_login:
            # §3.3: bring Chrome forward on the app once per expiry, not a new
            # tab every poll while the person is typing their password.
            self._chrome.open_login()
        self._asked_login = status == "needs_login"
        self._show(status, self._worker.detail)
        self._keep_current(status)
        return True

    def _keep_current(self, status: str) -> None:
        """§3.7.4–§3.7.6, on this thread (the zip comes through Playwright):
        connected, confirm this version and check when due; refused, update
        from the release the refusal named. Signed out or unreachable, the
        backend cannot be asked — nothing to do."""
        if self._updater is None or status not in ("connected", "update_required"):
            return
        try:
            if status == "connected":
                self._updater.connected()
                self._updater.tick()
            else:
                self._updater.tick(required=self._worker.manifest)
        except Exception:  # noqa: BLE001 — an update is never worth the helper
            logger.exception("update check")

    def run(self) -> None:
        self.open()
        while self.step(self._poll_s):
            pass

    def _apply(self, command: str) -> None:
        if self._worker is None:
            return
        if command == "login":
            self._chrome.open_login()
        elif command == "pause":
            self._worker.paused = True
        else:  # "resume" — the window sends nothing else
            self._worker.paused = False

    def _close(self) -> None:
        if self._chrome is not None:
            try:
                self._chrome.close()
            except Exception:  # noqa: BLE001 — ending anyway
                logger.exception("closing Chrome")

    def _show(self, status: str, detail: str) -> None:
        with self._lock:
            self._snapshot = (status, detail)


def _driver_paths() -> tuple[str, str]:
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    return str(node), str(cli)


def self_test() -> int:
    """`python -m outside_helper --self-test` (§3.7.4 step 4): a new version
    counts as installed only once this passes in its own venv."""
    from importlib.metadata import version as dist_version
    from pathlib import Path

    from .version import __version__

    try:
        import playwright.sync_api  # noqa: F401

        missing = [p for p in _driver_paths() if not Path(p).exists()]
    except Exception as exc:  # noqa: BLE001 — any of it means "cannot drive Chrome"
        print(f"outside-helper {__version__}: playwright unusable: {exc}")
        return 1
    if missing:
        print(f"outside-helper {__version__}: playwright driver missing: {', '.join(missing)}")
        return 1
    print(f"outside-helper {__version__}, playwright {dist_version('playwright')}: ok")
    return 0


def _ask_app_url(tk_root: Any) -> str | None:  # pragma: no cover — needs a display
    from tkinter import simpledialog

    while True:
        url = simpledialog.askstring(
            "Outside Helper",
            "AI 工作區的網址(例如 https://ai.example.corp):",
            parent=tk_root,
        )
        if url is None:
            return None
        url = url.strip().rstrip("/")
        if url.startswith(("https://", "http://")):
            return url


def main() -> int:  # pragma: no cover — the window needs a display; verified by hand (D14)
    if "--self-test" in sys.argv[1:]:
        return self_test()
    import tkinter as tk
    from tkinter import messagebox

    from .backend import Backend
    from .builtin_errands import build as build_errands
    from .chrome import Chrome
    from .home import InstanceLock, profile_dir, read_settings, root, write_settings
    from .version import __version__
    from .worker import Worker

    home = root()
    (home / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                home / "logs" / "helper.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        ],
    )

    window = tk.Tk()
    window.title(f"Outside Helper {__version__}")
    lock = InstanceLock(home / "helper.lock")
    if not lock.acquire():
        messagebox.showinfo("Outside Helper", "外網小幫手已經在執行了。")
        return 0

    settings = read_settings(home)
    app_url = settings.get("app_url")
    if not isinstance(app_url, str) or not app_url:
        app_url = _ask_app_url(window)
        if app_url is None:
            return 0
        write_settings(home, {**settings, "app_url": app_url})

    import platform
    import shutil
    import subprocess
    import tempfile
    from importlib.metadata import version as dist_version
    from pathlib import Path

    from . import setup_core
    from .updates import Updater

    updater_box: list[Updater] = []
    chrome_version: list[str] = []

    def make_worker(chrome: Chrome) -> Worker:
        backend = Backend(chrome.send, fetch_bytes=chrome.fetch_bytes)
        updater_box.append(Updater(home, backend))

        def info() -> dict[str, str]:
            if not chrome_version:
                try:
                    chrome_version.append(chrome.version())
                except Exception:  # noqa: BLE001 — a report, not a reason to stop
                    chrome_version.append("")
            return {
                "chrome": chrome_version[0],
                "playwright": dist_version("playwright"),
                "python": platform.python_version(),
                "update": updater_box[0].report(),
            }

        return Worker(backend, chrome, errands=build_errands(backend.lookup_targets), info=info)

    ctl = Controller(
        lambda: Chrome(profile_dir(home), app_url),
        make_worker,
        make_updater=lambda _chrome: updater_box[0],
    )
    thread = threading.Thread(target=ctl.run, name="outside-helper", daemon=True)
    thread.start()

    status_var = tk.StringVar(value=describe("starting", ""))
    update_var = tk.StringVar(value="")
    tk.Label(
        window, textvariable=status_var, wraplength=380, justify="left", padx=12, pady=12
    ).pack(fill="x")
    tk.Label(window, text=app_url, fg="#666", padx=12).pack(fill="x")
    tk.Label(window, textvariable=update_var, wraplength=380, justify="left", padx=12).pack(
        fill="x"
    )
    buttons = tk.Frame(window, padx=12, pady=12)
    buttons.pack(fill="x")
    pause_text = tk.StringVar(value="暫停")
    state = setup_core.read_state(home)
    base_python = str(state.get("base_python") or sys.executable)

    def toggle_pause() -> None:
        paused = pause_text.get() == "暫停"
        ctl.send("pause" if paused else "resume")
        pause_text.set("繼續" if paused else "暫停")

    def end(command: str = "quit") -> None:
        ctl.send(command)
        thread.join(timeout=10)
        lock.release()
        window.destroy()

    def restart() -> None:
        # §3.7.6: end, and let the fixed launcher start the version now ready
        end("restart")
        subprocess.Popen([base_python, str(home / "launch.pyw")], cwd=str(home))

    def remove() -> None:
        if not messagebox.askyesno(
            "Outside Helper", "會刪掉外網小幫手和它的登入資料(它自己的 Chrome 設定檔)。確定嗎?"
        ):
            return
        # D17: this program's own files cannot be removed while it runs, so a
        # copy of the clean-up code runs from %TEMP% once it has ended
        script = Path(tempfile.mkdtemp(prefix="outside-helper-")) / "setup_core.py"
        shutil.copy2(setup_core.__file__, script)
        end()
        subprocess.Popen(
            [base_python, str(script), "--uninstall", str(home)], cwd=str(script.parent)
        )

    startup_var = tk.BooleanVar(value=bool(state.get("start_at_login")))

    def toggle_startup() -> None:
        on = startup_var.get()
        try:
            setup_core.run_quietly(
                setup_core.startup_command(on, base_python=base_python, home=home)
            )
            setup_core.write_state(home, {**setup_core.read_state(home), "start_at_login": on})
        except setup_core.SetupFailed as exc:
            startup_var.set(not on)
            messagebox.showerror("Outside Helper", str(exc))

    tk.Button(buttons, text="登入", command=lambda: ctl.send("login")).pack(side="left")
    tk.Button(buttons, textvariable=pause_text, command=toggle_pause).pack(side="left", padx=6)
    restart_button = tk.Button(buttons, text="重新開啟", command=restart)
    tk.Button(buttons, text="結束", command=end).pack(side="right")
    options = tk.Frame(window, padx=12)
    options.pack(fill="x", pady=(0, 12))
    tk.Checkbutton(
        options, text="開機時自動啟動", variable=startup_var, command=toggle_startup
    ).pack(side="left")
    tk.Button(options, text="解除安裝", command=remove).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", end)

    def refresh() -> None:
        status, detail = ctl.snapshot()
        status_var.set(describe(status, detail))
        update = ctl.update_status()
        update_var.set(describe_update(update, forced=status == "update_required"))
        if update == "ready":
            restart_button.pack(side="left")
        else:
            restart_button.pack_forget()
        window.title(
            f"正在查:{detail} — Outside Helper"
            if status == "connected" and detail
            else f"Outside Helper {__version__}"
        )
        show = home / setup_core.SHOW_REQUEST
        if show.exists():  # launched again: come forward instead (§3.7.5)
            show.unlink(missing_ok=True)
            window.deiconify()
            window.lift()
            window.focus_force()
        window.after(500, refresh)

    refresh()
    window.minsize(420, 0)
    window.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
