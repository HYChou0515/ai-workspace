"""Prebuild every package in `tooling.packages.PACKAGES` into a
SELF-CONTAINED bundle the sandbox can drop in at provision time.

    uv run python scripts/prebuild_tools.py

For each package, this writes under ``PREBUILT_DIR/<name>/``:

  - ``.venv/``           uv-built relocatable venv with the package installed
  - ``python/``          bundled portable cpython
  - ``launch``           jail-safe shell launcher (AT_SECURE workaround)
  - ``commands.json``    cached from `launch` (3-stage contract stage 1)
  - ``schemas/<cmd>.json`` cached from `launch <cmd>` (stage 2)
  - ``.built``           mtime marker for incremental rebuild

Packages whose source dir is missing are skipped with a warning — this
keeps `rca-tools` (a local-only in-house package) optional: a fresh
clone without that source dir still builds cleanly.

Run `uv run python -m workspace_app` afterwards.
"""

from __future__ import annotations

import argparse

from workspace_app.tooling.packages import PACKAGES, PREBUILT_DIR
from workspace_app.tooling.prebuild import build_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Prebuild RCA tool-package bundles.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild every package even when its source content is unchanged.",
    )
    args = parser.parse_args()

    print(f"Prebuilding tool packages into {PREBUILT_DIR}")
    PREBUILT_DIR.mkdir(parents=True, exist_ok=True)
    for name, source in PACKAGES.items():
        if not source.is_dir():
            print(f"  ⏭  skipping {name}: source dir missing ({source})")
            continue
        print(f"  ▶  building {name} from {source} …")
        build_package(name=name, source=source, dst=PREBUILT_DIR / name, force=args.force)
    print("Done. Run `uv run python -m workspace_app`.")


if __name__ == "__main__":
    main()
