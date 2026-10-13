"""The helper's window (docs/plan-outside-helper.md D13) and the thread behind it.

The window (tkinter, in the standard library) shows a status line, a line
about the next version, the buttons 登入, 暫停/繼續, 結束 (and 重新開啟 once a
new version is ready), the 「開機時自動啟動」 box, 重設登入 and 解除安裝 — and draws no
state it did not read from the controller. The controller lives on ONE worker thread: it owns the
Chrome (Playwright's sync API must stay on the thread that started it) and the
`Worker` loop, and takes the buttons as commands through a queue. Closing the
window ends the helper.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
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
    "connected": "已連線，等待查詢",
    "paused": "已暫停：AI 會請你自己查",
    # never signed in on this run vs signed in and then lapsed (Controller)
    "needs_login": "請在小幫手開的 Chrome 視窗登入 AI Workspace",
    "login_expired": "登入已過期，請在小幫手開的 Chrome 視窗重新登入",
    "update_required": "這個版本已停用，需要更新",
    "unreachable": "連不上 AI Workspace，會自動重試",
    "error": "發生錯誤",
    "no_chrome": "開不了 Chrome。Chrome 剛更新的話，請更新小幫手；或按「重設登入」",
}

#: What each errand is doing, as the window says it.
_DOING = {"search": "正在搜尋", "open_url": "正在開啟"}
#: The 查詢紀錄 tab's name for each errand.
_KIND = {"search": "搜尋", "open_url": "開啟網頁"}

logger = logging.getLogger(__name__)


def describe(status: str, detail: str, kind: str = "", site: str = "") -> str:
    """The status line the person reads: while an errand runs, what it does
    (「正在搜尋（Google）：pandas 2.0」); otherwise the status, with any reason
    on a line of its own."""
    if status == "connected" and detail:
        doing = _DOING.get(kind, "正在查")
        return f"{doing}（{site}）：{detail}" if site else f"{doing}：{detail}"
    text = _TEXT.get(status, status)
    return f"{text}\n{detail}" if detail else text


def history_row(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """One row of the 查詢紀錄 tab: (when, what, which, how it ended)."""
    at = str(row.get("at", ""))
    when = f"{at[5:10]} {at[11:16]}" if len(at) >= 16 else at
    kind = str(row.get("kind", ""))
    what = _KIND.get(kind, kind)
    if row.get("site"):
        what = f"{what}（{row['site']}）"
    if row.get("outcome") == "done":
        ended = "已送給 AI"
    else:
        ended = f"沒查到：{row.get('reason', '')}" if row.get("reason") else "沒查到"
    return when, what, str(row.get("args", "")), ended


def log_tail(path: Any, *, lines: int = 300) -> str:
    """The 記錄檔 tab: the last `lines` of helper.log ("" before there is one)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "".join(text.splitlines(keepends=True)[-lines:])


def describe_update(status: str, *, forced: bool) -> str:
    """The window's line about the next version; `forced` when this version is
    refused (§3.7.6)."""
    if status == "preparing":
        return "正在準備新版…"
    if status == "ready":
        return "新版已下載，按「立即更新」換上" if forced else "新版已下載，下次開啟時套用"
    if status.startswith("failed"):
        why = status.partition(":")[2].strip()
        if forced:
            return (
                f"新版準備失敗：{why}。請到 AI Workspace 的「小幫手」頁重新下載、執行 install.pyw"
            )
        return f"新版準備失敗：{why}（這一版照常使用）"
    return ""


#: Statuses that show this version runs — a waiting version confirms itself
#: on them (§3.7.5). Not "error" (the backend refused its requests) nor
#: "unreachable" (its line to the backend may be what is broken): those may be
#: the version's own fault, and a confirmed version is never rolled back.
_CONFIRMS = ("connected", "needs_login", "paused", "update_required")


class Controller:
    def __init__(
        self,
        make_chrome: Callable[[], Any],
        make_worker: Callable[[Any], Any],
        *,
        make_updater: Callable[[Any], Any] | None = None,
        reset_profile: Callable[[], None] | None = None,
        poll_s: float = POLL_S,
        history: Any = None,
    ) -> None:
        self._make_chrome = make_chrome
        self._make_worker = make_worker
        self._make_updater = make_updater
        self._reset_profile = reset_profile
        self._poll_s = poll_s
        self._commands: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._snapshot: tuple[str, str, str, str] = ("starting", "", "", "")
        #: Signed in at least once on this run: a later sign-out is an expiry.
        self._signed_in = False
        #: The 查詢紀錄 the window shows (history.History), or None.
        self.history = history
        self._chrome: Any = None
        self._worker: Any = None
        self._updater: Any = None
        self._asked_login = False
        #: Set by 「重新開啟」: the window starts the launcher once this ends.
        self.restart = False

    # ── the window's side (any thread) ──────────────────────────────────────

    def send(self, command: str) -> None:
        self._commands.put(command)

    def snapshot(self) -> tuple[str, str, str, str]:
        """(status, detail, kind, site) — `describe`'s arguments."""
        with self._lock:
            return self._snapshot

    def update_status(self) -> str:
        """The updater's: "" | "preparing" | "ready" | "failed: …"."""
        return str(getattr(self._updater, "status", "") or "")

    # ── the worker thread ───────────────────────────────────────────────────

    def open(self) -> None:
        # what a previous Chrome left is dead: never drive it again
        self._chrome = self._worker = None
        try:
            self._chrome = self._make_chrome()
        except Exception as exc:  # noqa: BLE001 — shown to the person, who can only quit
            logger.exception("could not start Chrome")
            self._show("no_chrome", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
            return
        self._worker = self._make_worker(self._chrome)
        if hasattr(self._worker, "on_change"):
            self._worker.on_change = self._show
        if self._make_updater is not None and self._updater is None:
            # one for the helper's life: a second would prepare the same
            # version again over the first one's files
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
            if command == "reset_login":
                if not self._reset_login():
                    return True  # leave its reason on screen until the next poll
            else:
                self._apply(command)
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                command = None
        if self._worker is None:
            return True  # Chrome never started: nothing to poll
        if getattr(self._chrome, "gone", False):
            # the person closed the helper's Chrome window: open it again
            paused = self._worker.paused
            self._close()
            self.open()
            if self._worker is None:
                return True
            self._worker.paused = paused
        self._worker.tick()
        status = self._worker.status
        if status == "needs_login" and not self._asked_login:
            # §3.3: bring Chrome forward on the app once per expiry, not a new
            # tab every poll while the person is typing their password.
            self._chrome.open_login()
        self._asked_login = status == "needs_login"
        self._signed_in = self._signed_in or status == "connected"
        shown = "login_expired" if status == "needs_login" and self._signed_in else status
        self._show(
            shown,
            self._worker.detail,
            getattr(self._worker, "kind", ""),
            getattr(self._worker, "site", ""),
        )
        self._keep_current(status)
        return True

    def _keep_current(self, status: str) -> None:
        """§3.7.4–§3.7.6, on this thread (the zip comes through Playwright).
        A tick that ran means this version is up — signed out, paused or
        offline included, none of which is the version's doing — so a waiting
        version confirms itself (§3.7.5). Connected, check for a newer one
        when due; refused, update from the release the refusal named."""
        if self._updater is None:
            return
        try:
            if status in _CONFIRMS:
                self._updater.connected()
            if status == "connected":
                self._updater.tick()
            elif status == "update_required":
                self._updater.tick(required=self._worker.manifest)
        except Exception:  # noqa: BLE001 — an update is never worth the helper
            logger.exception("update check")

    def run(self) -> None:
        self.open()
        while True:
            try:
                if not self.step(self._poll_s):
                    return
            except Exception as exc:  # noqa: BLE001 — the window must not freeze on it
                logger.exception("helper loop")
                self._show("error", str(exc).splitlines()[0] if str(exc) else type(exc).__name__)

    def _reset_login(self) -> bool:
        """「重設登入」 (§3.7.7): close Chrome — it holds its profile — move
        the profile aside, start again on a fresh one and open the login.
        False when the profile could not be moved (still held — Windows): the
        reason is on screen and Chrome reopens on the old profile."""
        self._close()
        failed = ""
        if self._reset_profile is not None:
            try:
                self._reset_profile()
            except OSError as exc:
                failed = str(exc) or type(exc).__name__
                logger.warning("reset login", exc_info=True)
        self.open()
        if failed:
            self._show("error", f"重設登入沒有成功：{failed}")
            return False
        if self._chrome is not None and self._worker is not None:
            self._chrome.open_login()
        return True

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

    def _show(self, status: str, detail: str, kind: str = "", site: str = "") -> None:
        with self._lock:
            self._snapshot = (status, detail, kind, site)


def make_controller(home: Any, app_url: str, make_chrome: Callable[[], Any]) -> Controller:
    """The helper as the window runs it: on the Chrome `make_chrome` opens, a
    Backend over its signed-in line, the job loop with the shipped errands, the
    updater, and what each check-in reports (§3.7.7). The window and the live
    check (scripts/check_ai_workspace_helper.py) both start it from here."""
    import platform
    from importlib.metadata import version as dist_version

    from .backend import Backend
    from .builtin_errands import build as build_errands
    from .history import History
    from .updates import Updater
    from .worker import Worker

    backends: list[Backend] = []
    chrome_version: list[str] = []

    class _Latest:
        """The current Chrome's backend: Chrome is reopened when the person
        closes it, the one updater outlives it."""

        def __getattr__(self, name: str) -> Any:
            return getattr(backends[-1], name)

    updater = Updater(home, _Latest())
    history = History(home / "history.jsonl")

    def make_worker(chrome: Any) -> Worker:
        backend = Backend(chrome.send, fetch_bytes=chrome.fetch_bytes)
        backends.append(backend)
        del backends[:-1]

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
                "update": updater.report(),
            }

        return Worker(
            backend,
            chrome,
            errands=build_errands(backend.lookup_targets),
            info=info,
            history=history,
        )

    from .home import reset_profile

    return Controller(
        make_chrome,
        make_worker,
        make_updater=lambda _chrome: updater,
        reset_profile=lambda: reset_profile(home),
        history=history,
    )


def _driver_paths() -> tuple[str, str]:
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    return str(node), str(cli)


def self_test() -> int:
    """`python -m ai_workspace_helper --self-test` (§3.7.4 step 4): a new version
    counts as installed only once this passes in its own venv."""
    from importlib.metadata import version as dist_version
    from pathlib import Path

    from .version import __version__

    try:
        import playwright.sync_api  # noqa: F401

        missing = [p for p in _driver_paths() if not Path(p).exists()]
    except Exception as exc:  # noqa: BLE001 — any of it means "cannot drive Chrome"
        print(f"ai-workspace-helper {__version__}: playwright unusable: {exc}")
        return 1
    if missing:
        print(f"ai-workspace-helper {__version__}: playwright driver missing: {', '.join(missing)}")
        return 1
    print(f"ai-workspace-helper {__version__}, playwright {dist_version('playwright')}: ok")
    return 0


def _ask_app_url(tk_root: Any) -> str | None:  # pragma: no cover — needs a display
    from tkinter import simpledialog

    while True:
        url = simpledialog.askstring(
            "AI Workspace Helper",
            "AI Workspace 的網址（例如 https://ai.example.corp）：",
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

    from .chrome import Chrome
    from .home import InstanceLock, profile_dir, read_settings, root, write_settings
    from .version import __version__

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
    window.title(f"AI Workspace Helper {__version__}")
    lock = InstanceLock(home / "helper.lock")
    if not lock.acquire():
        messagebox.showinfo("AI Workspace Helper", "小幫手已經在執行了。")
        return 0

    settings = read_settings(home)
    app_url = settings.get("app_url")
    if not isinstance(app_url, str) or not app_url:
        # only the question on screen — not an empty window behind it
        window.withdraw()
        app_url = _ask_app_url(window)
        if app_url is None:
            return 0
        write_settings(home, {**settings, "app_url": app_url})
        window.deiconify()

    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from . import setup_core

    # Developer knobs, unset on the person's PC: a Linux box has Chromium, not
    # Chrome; a recording places the window.
    channel = os.environ.get("AI_WORKSPACE_HELPER_CHROME_CHANNEL") or "chrome"
    chrome_args = os.environ.get("AI_WORKSPACE_HELPER_CHROME_ARGS", "").split()
    ctl = make_controller(
        home,
        app_url,
        lambda: Chrome(profile_dir(home), app_url, channel=channel, args=chrome_args),
    )
    thread = threading.Thread(target=ctl.run, name="ai-workspace-helper", daemon=True)
    thread.start()

    from tkinter import ttk

    state = setup_core.read_state(home)
    base_python = str(state.get("base_python") or sys.executable)

    # Three tabs (a tab strip — switching panels, CLAUDE.md UI rule): what it
    # is doing now, what it has looked up, and its log.
    tabs = ttk.Notebook(window)
    tabs.pack(fill="both", expand=True, padx=8, pady=8)
    status_tab = ttk.Frame(tabs, padding=12)
    history_tab = ttk.Frame(tabs, padding=12)
    log_tab = ttk.Frame(tabs, padding=12)
    tabs.add(status_tab, text="狀態")
    tabs.add(history_tab, text="查詢紀錄")
    tabs.add(log_tab, text="記錄檔")

    # ── 狀態 ────────────────────────────────────────────────────────────────
    status_var = tk.StringVar(value=describe("starting", ""))
    update_var = tk.StringVar(value="")
    ttk.Label(status_tab, textvariable=status_var, wraplength=460, justify="left").pack(
        fill="x", pady=(0, 6)
    )
    ttk.Label(status_tab, text=app_url, foreground="#666").pack(fill="x")
    ttk.Label(status_tab, textvariable=update_var, wraplength=460, justify="left").pack(
        fill="x", pady=(6, 0)
    )
    buttons = ttk.Frame(status_tab)
    buttons.pack(fill="x", pady=(12, 8))
    pause_text = tk.StringVar(value="暫停")

    def toggle_pause() -> None:
        paused = pause_text.get() == "暫停"
        ctl.send("pause" if paused else "resume")
        pause_text.set("繼續" if paused else "暫停")

    def end(command: str = "quit") -> None:
        # The lock is NOT released here: it goes with this process, which may
        # still hold Chrome's profile for a moment — the launcher (restart) and
        # the clean-up (uninstall) wait for it, so neither races that.
        ctl.send(command)
        thread.join(timeout=10)
        window.destroy()

    def restart() -> None:
        # §3.7.6: end, and let the fixed launcher start the version now ready
        end("restart")
        subprocess.Popen([base_python, str(home / "launch.pyw"), "--wait"], cwd=str(home))

    def remove() -> None:
        if not messagebox.askyesno(
            "AI Workspace Helper",
            "會刪掉小幫手和它的登入資料（它自己的 Chrome 設定檔）。確定嗎？",
        ):
            return
        # D17: this program's own files cannot be removed while it runs, so a
        # copy of the clean-up code runs from %TEMP% once it has ended
        script = Path(tempfile.mkdtemp(prefix="ai-workspace-helper-")) / "setup_core.py"
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
            messagebox.showerror("AI Workspace Helper", str(exc))

    def reset_login() -> None:
        if messagebox.askyesno(
            "AI Workspace Helper",
            "重設登入：把小幫手的 Chrome 設定檔移到旁邊，重新開一個再登入。要繼續嗎？",
        ):
            ctl.send("reset_login")

    ttk.Button(buttons, text="登入", command=lambda: ctl.send("login")).pack(side="left")
    ttk.Button(buttons, textvariable=pause_text, command=toggle_pause).pack(side="left", padx=6)
    restart_button = ttk.Button(buttons, text="立即更新", command=restart)
    ttk.Button(buttons, text="結束", command=end).pack(side="right")
    options = ttk.Frame(status_tab)
    options.pack(fill="x")
    ttk.Checkbutton(
        options, text="開機時自動啟動", variable=startup_var, command=toggle_startup
    ).pack(side="left")
    ttk.Button(options, text="解除安裝", command=remove).pack(side="right")
    ttk.Button(options, text="重設登入", command=reset_login).pack(side="right", padx=6)
    window.protocol("WM_DELETE_WINDOW", end)

    # ── 查詢紀錄 ────────────────────────────────────────────────────────────
    ttk.Label(history_tab, text="最近 200 筆，只存在這台電腦。", foreground="#666").pack(
        fill="x", pady=(0, 6)
    )
    columns = ("when", "what", "args", "ended")
    table = ttk.Treeview(history_tab, columns=columns, show="headings", height=12)
    for column, heading, width in zip(
        columns, ("時間", "類型", "內容", "結果"), (90, 110, 220, 140), strict=True
    ):
        table.heading(column, text=heading, anchor="w")
        table.column(column, width=width, anchor="w", stretch=column in ("args", "ended"))
    table_scroll = ttk.Scrollbar(history_tab, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=table_scroll.set)
    table_scroll.pack(side="right", fill="y")
    table.pack(fill="both", expand=True)
    shown_history = [-1]

    # ── 記錄檔 ──────────────────────────────────────────────────────────────
    log_path = home / "logs" / "helper.log"

    def open_logs() -> None:
        folder = str(home / "logs")
        startfile = getattr(os, "startfile", None)  # Windows only
        if startfile is not None:
            startfile(folder)
        else:
            subprocess.Popen(["xdg-open", folder])

    log_bar = ttk.Frame(log_tab)
    log_bar.pack(fill="x", pady=(0, 6))
    ttk.Label(log_bar, text="出問題時，請附上這個記錄檔。", foreground="#666").pack(side="left")
    ttk.Button(log_bar, text="開啟記錄資料夾", command=open_logs).pack(side="right")
    log_text = tk.Text(log_tab, height=14, wrap="none", font=("TkFixedFont", 9))
    log_scroll = ttk.Scrollbar(log_tab, orient="vertical", command=log_text.yview)
    log_text.configure(yscrollcommand=log_scroll.set, state="disabled")
    log_scroll.pack(side="right", fill="y")
    log_text.pack(fill="both", expand=True)
    shown_log = [""]

    def refresh() -> None:
        status, detail, kind, site = ctl.snapshot()
        status_var.set(describe(status, detail, kind, site))
        update = ctl.update_status()
        update_var.set(describe_update(update, forced=status == "update_required"))
        if update == "ready":
            restart_button.pack(side="left")
        else:
            restart_button.pack_forget()
        window.title(
            f"{describe(status, detail, kind, site)} — AI Workspace Helper"
            if status == "connected" and detail
            else f"AI Workspace Helper {__version__}"
        )
        if ctl.history is not None and ctl.history.version != shown_history[0]:
            shown_history[0] = ctl.history.version
            table.delete(*table.get_children())
            for row in ctl.history.recent():
                table.insert("", "end", values=history_row(row))
        if tabs.index(tabs.select()) == 2:
            text = log_tail(log_path)
            if text != shown_log[0]:
                shown_log[0] = text
                log_text.configure(state="normal")
                log_text.delete("1.0", "end")
                log_text.insert("end", text)
                log_text.see("end")
                log_text.configure(state="disabled")
        show = home / setup_core.SHOW_REQUEST
        if show.exists():  # launched again: come forward instead (§3.7.5)
            show.unlink(missing_ok=True)
            window.deiconify()
            window.lift()
            window.focus_force()
        window.after(500, refresh)

    refresh()
    window.minsize(520, 0)
    window.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
