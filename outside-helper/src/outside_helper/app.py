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


class Controller:
    def __init__(
        self,
        make_chrome: Callable[[], Any],
        make_worker: Callable[[Any], Any],
        *,
        poll_s: float = POLL_S,
    ) -> None:
        self._make_chrome = make_chrome
        self._make_worker = make_worker
        self._poll_s = poll_s
        self._commands: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._snapshot = ("starting", "")
        self._chrome: Any = None
        self._worker: Any = None
        self._asked_login = False

    # ── the window's side (any thread) ──────────────────────────────────────

    def send(self, command: str) -> None:
        self._commands.put(command)

    def snapshot(self) -> tuple[str, str]:
        with self._lock:
            return self._snapshot

    # ── the worker thread ───────────────────────────────────────────────────

    def open(self) -> None:
        try:
            self._chrome = self._make_chrome()
        except Exception as exc:  # noqa: BLE001 — shown to the person, who can only quit
            logger.exception("could not start Chrome")
            self._show("no_chrome", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
            return
        self._worker = self._make_worker(self._chrome)

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
            if command == "quit":
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
        return True

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
    import tkinter as tk
    from tkinter import messagebox

    from .backend import Backend
    from .builtin_errands import BUILTIN
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

    ctl = Controller(
        lambda: Chrome(profile_dir(home), app_url),
        lambda chrome: Worker(Backend(chrome.send), chrome, errands=BUILTIN),
    )
    thread = threading.Thread(target=ctl.run, name="outside-helper", daemon=True)
    thread.start()

    status_var = tk.StringVar(value=describe("starting", ""))
    tk.Label(
        window, textvariable=status_var, wraplength=360, justify="left", padx=12, pady=12
    ).pack(fill="x")
    tk.Label(window, text=app_url, fg="#666", padx=12).pack(fill="x")
    buttons = tk.Frame(window, padx=12, pady=12)
    buttons.pack(fill="x")
    pause_text = tk.StringVar(value="暫停")

    def toggle_pause() -> None:
        paused = pause_text.get() == "暫停"
        ctl.send("pause" if paused else "resume")
        pause_text.set("繼續" if paused else "暫停")

    def quit_helper() -> None:
        ctl.send("quit")
        thread.join(timeout=10)
        lock.release()
        window.destroy()

    tk.Button(buttons, text="登入", command=lambda: ctl.send("login")).pack(side="left")
    tk.Button(buttons, textvariable=pause_text, command=toggle_pause).pack(side="left", padx=6)
    tk.Button(buttons, text="結束", command=quit_helper).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", quit_helper)

    def refresh() -> None:
        status, detail = ctl.snapshot()
        status_var.set(describe(status, detail))
        window.title(
            f"正在查:{detail} — Outside Helper"
            if status == "connected" and detail
            else f"Outside Helper {__version__}"
        )
        window.after(500, refresh)

    refresh()
    window.minsize(400, 0)
    window.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
