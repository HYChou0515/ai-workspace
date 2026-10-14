"""The helper's fixed launcher (docs/plan-outside-helper.md §3.7.5).

Both shortcuts point here, run by the Python the person confirmed at install
(its absolute pythonw.exe path — D16). Standard library only: it lives in
%LOCALAPPDATA%\\AIWorkspaceHelper\\ beside setup_core.py and state.json, outside
every version folder, so an update never has to replace a running file. What
it decides — which version, the rollback, one helper at a time — is
`setup_core.launch`, tested; this file only shows a failure.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setup_core  # noqa: E402  # ty: ignore[unresolved-import] — beside this file, by design


def main() -> int:
    try:
        setup_core.launch(setup_core.launcher_home(HERE))
    except (setup_core.SetupFailed, OSError) as exc:
        import tkinter
        from tkinter import messagebox

        tkinter.Tk().withdraw()
        messagebox.showerror("AI Workspace Helper", str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
