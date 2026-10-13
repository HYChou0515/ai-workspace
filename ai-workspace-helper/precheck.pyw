"""May this PC run the helper? (docs/plan-outside-helper.md D19): unzip, then
double-click this file BEFORE install.pyw.

Standard library only, and it installs nothing. Without admin rights the
helper still needs a few things a company PC may refuse: running programs
under %LOCALAPPDATA% (its venv, Playwright's node.exe), PowerShell for the
shortcut, Chrome, and Chrome not barred from automation. Each is tried for
real in a scratch folder beside where the helper would live, which is
removed afterwards; the result can be copied for IT.
"""

import sys
import threading
import tkinter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setup_core  # noqa: E402  # ty: ignore[unresolved-import] — beside this file in the zip


def main() -> int:
    window = tkinter.Tk()
    window.title("AI Workspace Helper — 預檢")
    tkinter.Label(
        window,
        text="只檢查這台電腦能不能跑小幫手，不安裝任何東西。",
        padx=16,
        pady=12,
        anchor="w",
    ).pack(fill="x")
    text = tkinter.Text(window, width=78, height=14, wrap="word", padx=8, pady=8)
    text.insert("end", "檢查中…")
    text.configure(state="disabled")
    text.pack(fill="both", expand=True, padx=12)
    result: dict[str, str] = {}

    def show(summary: str) -> None:
        result["summary"] = summary
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", summary)
        text.configure(state="disabled")

    def copy() -> None:
        window.clipboard_clear()
        window.clipboard_append(result.get("summary", ""))

    buttons = tkinter.Frame(window, padx=12, pady=12)
    buttons.pack(fill="x")
    tkinter.Button(buttons, text="複製結果", command=copy).pack(side="left")
    tkinter.Button(buttons, text="關閉", command=window.destroy).pack(side="right")

    def work() -> None:
        home = setup_core.root()
        python = sys.executable
        pythonw = Path(python).with_name("pythonw.exe")
        checks = setup_core.precheck(
            HERE,
            home.with_name(home.name + "-precheck"),
            base_python=str(pythonw if pythonw.exists() else python),
            python_version=tuple(sys.version_info[:3]),
            chrome_paths=setup_core.chrome_install_paths(),
            chrome_policy=setup_core.chrome_policy,
        )
        summary = setup_core.precheck_summary(checks)
        window.after(0, lambda: show(summary))

    threading.Thread(target=work, daemon=True).start()
    window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
