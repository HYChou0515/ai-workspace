"""The outside helper's fixed launcher (docs/plan-outside-helper.md §3.7.5).

Both shortcuts point here, run by the Python the person confirmed at install
(its absolute pythonw.exe path — D16). Standard library only: it lives in
%LOCALAPPDATA%\\OutsideHelper\\ beside setup_core.py and state.json, outside
every version folder, so an update never has to replace a running file.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setup_core  # noqa: E402  # ty: ignore[unresolved-import] — beside this file, by design


def main() -> int:
    try:
        argv = setup_core.launch_command(HERE, setup_core.read_state(HERE))
        subprocess.Popen(argv, cwd=str(HERE))
    except (setup_core.SetupFailed, OSError) as exc:
        import tkinter
        from tkinter import messagebox

        tkinter.Tk().withdraw()
        messagebox.showerror("Outside Helper", str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
