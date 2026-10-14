"""Install the helper (docs/plan-outside-helper.md D16): unzip, then
double-click this file.

Standard library only — nothing is installed yet. It shows which Python it will
use (a PC may have several; the shortcut is tied to this one by its absolute
path), installs from the wheels in the zip with no network, and puts an
"AI Workspace Helper" shortcut on the desktop. If that is not the Python you meant,
cancel and run `py -3.12 install.pyw` (any 3.10–3.14) instead.
"""

import sys
import threading
import tkinter
from typing import Any
from pathlib import Path
from tkinter import messagebox

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setup_core  # noqa: E402  # ty: ignore[unresolved-import] — beside this file in the zip


def windowed_python() -> str:
    """pythonw.exe beside the running interpreter: the shortcut must not open
    a console window every time the helper starts."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe)


def main() -> int:
    window = tkinter.Tk()
    window.withdraw()
    python = windowed_python()
    version = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info < (3, 10):
        messagebox.showerror("AI Workspace Helper", f"需要 Python 3.10 以上，這是 {version}：\n{python}")
        return 1
    if not messagebox.askokcancel(
        "AI Workspace Helper",
        f"將使用 Python {version}\n{python}\n\n"
        "不是這個 Python 的話，按「取消」，改用 py -3.x install.pyw。",
    ):
        return 0

    window.deiconify()
    window.title("AI Workspace Helper")
    tkinter.Label(window, text="安裝中…（約一分鐘，不用連網）", padx=24, pady=24).pack()
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["installed"] = setup_core.install(HERE, setup_core.root(), base_python=python)
        except Exception as exc:  # noqa: BLE001 — shown to the person
            outcome["error"] = exc
        window.after(0, window.quit)

    threading.Thread(target=work, daemon=True).start()
    window.mainloop()
    window.withdraw()
    if "error" in outcome:
        messagebox.showerror("AI Workspace Helper", f"安裝失敗：\n{outcome['error']}")
        return 1
    installed = outcome["installed"]
    if installed.shortcut_error:
        # PowerShell refused (plan D19): installed all the same
        where = setup_core.root() / "launch.pyw"
        shortcut = (
            f"桌面捷徑沒有建成（{installed.shortcut_error}）。"
            f"之後請雙擊 {where} 開啟小幫手。"
        )
    else:
        shortcut = "桌面有「AI Workspace Helper」捷徑。"
    home = setup_core.root()
    done = f"小幫手 {installed.version} 已安裝。{shortcut}"
    if setup_core.helper_running(home):
        # D21 (§3.7.5 step 3): the version it replaced is still running
        if messagebox.askyesno(
            "AI Workspace Helper",
            f"{done}\n\n小幫手正在執行，要換成新版重新開啟嗎？",
        ) and setup_core.replace_running(home) == "shown":
            messagebox.showinfo(
                "AI Workspace Helper",
                "正在執行的小幫手沒有結束。請在它的視窗按「結束」，再開啟小幫手，就是新版。",
            )
    elif messagebox.askyesno("AI Workspace Helper", f"{done}\n\n現在開啟嗎？"):
        setup_core.launch(home)
    return 0


if __name__ == "__main__":
    sys.exit(main())
