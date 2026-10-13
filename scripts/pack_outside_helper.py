"""Pack the outside helper for the person's Windows PC
(docs/plan-outside-helper.md D14, §3.6, §3.7.2).

    python3 scripts/pack_outside_helper.py --version 0.3.0 [--min-version 0.2.0] [--out DIR]

Run on the dev server (it reaches PyPI; the PCs do not). Needs `pnpm` (builds
the card's converter into the helper, D11), `uv` (builds the helper's wheel)
and a Python with pip (downloads the Windows wheels). Writes into DIR (default
`dist/outside-helper/`):

- `ai-workspace-helper-<version>.zip` — `install.pyw`, `launch.pyw`, `setup_core.py`
  and `wheelhouse/`: the helper's wheel plus every package it needs as
  win_amd64 wheels for CPython 3.10–3.14, each pinned with `==`;
- `outside-helper.json` — `{version, min_version, file, sha256}`.

Put both in `server.outside_helper_dir` (§3.7.2); no redeploy. Before writing
the zip it checks that pip, given nothing but the wheelhouse, resolves the
helper and everything it needs on every one of those Pythons.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "outside-helper"

#: The CPython versions the zip installs on (D14) — greenlet ships one wheel
#: per minor, so each is downloaded for explicitly (§3.6).
PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]

#: Everything the helper needs, pinned. Playwright drives the installed Chrome
#: (`channel="chrome"`), so no browser is downloaded. This is NOT the repo's
#: own 1.49 pin (that one exists for the Debian 11 browser download): 1.49 pins
#: greenlet 3.1.1, which has no cp314 wheel. Resolved with
#: `pip download --platform win_amd64 --python-version 3.14 playwright==1.63.0`.
PINS = [
    "playwright==1.63.0",
    "greenlet==3.5.6",
    "pyee==13.0.1",
    "typing_extensions==4.16.0",
]

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

Run = Callable[..., None]


def _run(argv: list[str], cwd: Path | None = None) -> None:  # pragma: no cover — real processes
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True, cwd=cwd)


def _windows(version: str) -> list[str]:
    return [
        "--platform",
        "win_amd64",
        "--implementation",
        "cp",
        "--python-version",
        version,
        "--abi",
        "cp" + version.replace(".", ""),
        "--only-binary=:all:",
    ]


def download_command(pip: list[str], python: str, dest: Path) -> list[str]:
    """The pinned wheels for `python` on Windows. `--no-deps`: the pins ARE the
    closure; `verify_command` proves nothing is missing."""
    return [*pip, "download", *_windows(python), "--no-deps", "-d", str(dest), *PINS]


def verify_command(
    pip: list[str], python: str, wheelhouse: Path, version: str, target: Path
) -> list[str]:
    """Resolve the helper from the wheelhouse alone, as `install.pyw` will."""
    return [
        *pip,
        "install",
        "--dry-run",
        "--ignore-installed",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        *_windows(python),
        "--target",
        str(target),
        f"outside-helper=={version}",
    ]


def _stamped_copy(version: str, into: Path) -> Path:
    """The helper's source with `version` stamped in — a copy, so the source
    tree never carries a release's number."""
    copy = into / "outside-helper"
    shutil.copytree(HELPER, copy, ignore=shutil.ignore_patterns("__pycache__", "parity", "*.pyc"))
    version_py = copy / "src" / "outside_helper" / "version.py"
    text = version_py.read_text(encoding="utf-8")
    version_py.write_text(
        re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', text), encoding="utf-8"
    )
    return copy


def pack(
    version: str,
    out: Path,
    *,
    min_version: str = "0.0.0",
    pip: list[str] | None = None,
    run: Run = _run,
) -> tuple[Path, Path]:
    for v in (version, min_version):
        if not _VERSION.match(v):
            raise ValueError(f"a version is three numbers, like 1.4.0 — not {v!r}")
    pip = pip or [sys.executable, "-m", "pip"]
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"ai-workspace-helper-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="pack-outside-helper-") as tmp:
        work = Path(tmp)
        wheelhouse = work / "wheelhouse"
        wheelhouse.mkdir()
        # the card's converter, built from the frontend source into the package
        run(["pnpm", "run", "build:outside-helper"], cwd=ROOT / "web")
        source = _stamped_copy(version, work)
        run(["uv", "build", "--wheel", "-o", str(wheelhouse), str(source)])
        for python in PYTHONS:
            run(download_command(pip, python, wheelhouse))
        for python in PYTHONS:
            run(verify_command(pip, python, wheelhouse, version, work / f"target-{python}"))
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(HELPER / "install.pyw", "install.pyw")
            z.write(HELPER / "launch.pyw", "launch.pyw")
            z.write(HELPER / "src" / "outside_helper" / "setup_core.py", "setup_core.py")
            z.writestr(
                "README.txt",
                "AI Workspace Helper — 解壓縮後雙擊 install.pyw。\n"
                "Unzip, then double-click install.pyw.\n",
            )
            for wheel in sorted(wheelhouse.glob("*.whl")):
                z.write(wheel, f"wheelhouse/{wheel.name}")
    manifest = out / "outside-helper.json"
    manifest.write_text(
        json.dumps(
            {
                "version": version,
                "min_version": min_version,
                "file": zip_path.name,
                "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return zip_path, manifest


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — argument glue
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--min-version", default="0.0.0")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "outside-helper")
    args = parser.parse_args(argv)
    zip_path, manifest = pack(args.version, args.out, min_version=args.min_version)
    print(f"wrote {zip_path}\nwrote {manifest}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
