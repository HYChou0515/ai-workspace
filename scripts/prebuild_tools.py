"""Prebuild the sample provisioned tools into relocatable venv packages.

    uv run python scripts/prebuild_tools.py [--python X.Y | /path/to/python]

For each tool in `workspace_app.rca.sample_tools.SOURCES` this builds a
`uv venv --relocatable` under `RCA_TOOLS_DIR/<tool>/.venv` and installs the
tool into it. At runtime the app copies the whole package into the sandbox and
runs its venv binary — no uv / network / build step in the sandbox.

Pick the base python to match the SANDBOX, not your shell (a relocatable venv
still resolves its base python by path):
  - production LocalProcessSandbox in a py3.12 pod → `--python 3.12` (the pod's
    /usr/bin/python is overlaid into the jail);
  - local dev where the box's system python is too old → omit --python (uv's
    managed python) and run the app with SANDBOX_ISOLATE=false.

Then run the app normally:  uv run python -m workspace_app
and pick the `tool-demo` template in a new investigation.
"""

from __future__ import annotations

import subprocess
import sys

from workspace_app.rca.sample_tools import PREBUILT_DIR, SOURCES


def build_one(name: str, source, python: str | None) -> None:
    dst = PREBUILT_DIR / name
    venv = dst / ".venv"
    dst.mkdir(parents=True, exist_ok=True)
    py = ["--python", python] if python else []
    subprocess.run(["uv", "venv", "--relocatable", *py, str(venv)], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(venv), str(source)], check=True)
    print(f"  built {name} -> {dst}")


def main() -> None:
    python = sys.argv[1].removeprefix("--python").strip() or None if len(sys.argv) > 1 else None
    print(f"Prebuilding into {PREBUILT_DIR} (base python: {python or 'uv-managed'})")
    for name, source in SOURCES.items():
        if not source.is_dir():
            raise SystemExit(f"source repo missing: {source}")
        build_one(name, source, python)
    print("Done. Run `uv run python -m workspace_app` and pick the 'tool-demo' template.")


if __name__ == "__main__":
    main()
