"""Prebuild the sample provisioned tools into SELF-CONTAINED packages.

    uv run python scripts/prebuild_tools.py

For each tool in `workspace_app.rca.sample_tools.SOURCES` this builds, under
`RCA_TOOLS_DIR/<tool>/`:
  - `.venv/`   a `uv venv --relocatable` with the tool installed,
  - `python/`  a copy of the (standalone, portable) python the venv used,
  - `launch`   a tiny launcher.

At runtime the app copies the whole package into the sandbox and runs `launch`.
The package is fully self-contained — it brings its own python — so it runs in
the sandbox's userns chroot jail regardless of the host/pod python (you do NOT
need SANDBOX_ISOLATE=false, and you do NOT have to match the jail's python).

Why the launcher (not just `.venv/bin/<tool>`): inside the userns jail the
process is AT_SECURE, so glibc's loader ignores `$ORIGIN`/RPATH/LD_LIBRARY_PATH
and can't find the bundled libpython when python is started implicitly. The
launcher invokes the bundled python through the *explicit* dynamic loader (which
is not AT_SECURE) with the venv's site-packages on PYTHONPATH.

Then run the app normally:  uv run python -m workspace_app
and pick the `tool-demo` template in a new investigation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from workspace_app.rca.sample_tools import PREBUILT_DIR, SOURCES

# x86-64 / arm64 system loader; the launcher picks whichever exists in the jail.
_LAUNCH = """\
#!/bin/sh
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$here/.venv/lib/python{ver}/site-packages${{PYTHONPATH:+:$PYTHONPATH}}"
# Caches go to /tmp (writable, outside the workspace, never synced) — the tool
# dir may be a read-only mount, and ~/.cache would otherwise pollute the workspace.
export HOME=/tmp XDG_CACHE_HOME=/tmp/.cache MPLCONFIGDIR=/tmp/.config/matplotlib
ld=$(ls /lib64/ld-linux-x86-64.so.2 /lib/ld-linux-aarch64.so.1 2>/dev/null | head -n1)
exec "$ld" "$here/python/bin/python{ver}" "$here/.venv/bin/{tool}" "$@"
"""


def build_one(name: str, source: Path) -> None:
    dst = PREBUILT_DIR / name
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    venv = dst / ".venv"
    subprocess.run(["uv", "venv", "--relocatable", str(venv)], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(venv), str(source)], check=True)
    # Bundle the (portable) python the venv was built against, and derive its X.Y.
    real = (venv / "bin" / "python").resolve()  # .../cpython-X.Y.Z/bin/pythonX.Y
    ver = real.name.removeprefix("python")  # "3.13"
    shutil.copytree(real.parent.parent, dst / "python", symlinks=True)
    launch = dst / "launch"
    launch.write_text(_LAUNCH.format(ver=ver, tool=name))
    launch.chmod(0o755)
    print(f"  built {name} -> {dst}  (bundled python {ver})")


def main() -> None:
    print(f"Prebuilding self-contained tool packages into {PREBUILT_DIR}")
    for name, source in SOURCES.items():
        if not source.is_dir():
            raise SystemExit(f"source repo missing: {source}")
        build_one(name, source)
    print("Done. Run `uv run python -m workspace_app` and pick the 'tool-demo' template.")


if __name__ == "__main__":
    main()
