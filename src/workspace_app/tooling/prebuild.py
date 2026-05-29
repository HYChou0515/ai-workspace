"""Build a tool package into a self-contained, sandbox-droppable bundle.

The bundle layout (under ``dst/``)::

    .venv/                  uv-built relocatable venv with the package installed
    python/                 a copy of the (portable) cpython the venv was built with
    launch                  tiny shell launcher (handles AT_SECURE dynamic loader)
    commands.json           the package's command-list (cached from `launch`)
    schemas/<cmd>.json      one per command, the metadata + JSON schema
    .built                  empty marker — its mtime drives incremental rebuilds

The bundle is fully self-contained (its own python + venv + libs), so the
sandbox needs no uv / network / build step at provision time. ``launch``
forwards to ``python/bin/python <X.Y>`` through the explicit dynamic
loader so glibc's AT_SECURE rules don't strip ``$ORIGIN`` / ``RPATH`` /
``LD_LIBRARY_PATH`` inside the userns chroot jail.

See `docs/plan-skills-and-tools.md` §B.4.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

# Same launcher template as the legacy `scripts/prebuild_tools.py`: the
# AT_SECURE workaround for the jail is necessary, only the surrounding
# orchestration changed. ``{tool}`` becomes the package's bin entrypoint
# (= the [project.scripts] key, i.e. the package name).
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


def build_package(*, name: str, source: Path, dst: Path) -> None:
    """Build the package at ``source`` into the prebuilt bundle at ``dst``.

    Idempotent: skips when ``source`` hasn't changed since the last
    successful build (see ``_should_rebuild``)."""
    if not _should_rebuild(source, dst):
        return
    if dst.exists():
        shutil.rmtree(dst)
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
    # Sandbox-side `launch` is invoked through bash + a custom loader; here
    # at build time we want the host's venv-bin script directly so we can
    # actually run it without the jail's AT_SECURE setup. Use the venv's
    # console_script entrypoint as the "launch" for schema-dumping.
    _dump_schemas(venv / "bin" / name, dst)
    (dst / ".built").write_text("ok")


def _should_rebuild(source: Path, dst: Path) -> bool:
    """True iff ``dst`` is missing or any file under ``source`` (excluding
    dot-prefixed siblings — e.g. a freshly-rebuilt `.venv` left over from
    a previous prebuild run) is newer than ``dst/.built``."""
    marker = dst / ".built"
    if not marker.exists():
        return True
    built_at = marker.stat().st_mtime
    for path in source.rglob("*"):
        # Skip dot-prefixed dirs (e.g. .venv, .git) — those aren't
        # author-edited inputs to the build.
        if any(part.startswith(".") for part in path.relative_to(source).parts):
            continue
        if path.is_file() and path.stat().st_mtime > built_at:
            return True
    return False


def _dump_schemas(launch: Path, dst: Path) -> None:
    """Run the package's launch binary through the 3-stage contract and
    cache its self-description on disk under ``dst``.

    Writes ``dst/commands.json`` (the metadata array) plus
    ``dst/schemas/<cmd>.json`` (one per command). Raises ``RuntimeError``
    if the binary doesn't conform — the prebuild script halts loudly
    rather than caching a half-built bundle."""
    proc = subprocess.run([str(launch)], check=False, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{launch}` (bare) exited {proc.returncode}: "
            f"can't enumerate commands. stderr={proc.stderr.decode(errors='replace')!r}"
        )
    cmds = json.loads(proc.stdout.decode("utf-8"))
    (dst / "commands.json").write_text(json.dumps(cmds))
    schemas_dir = dst / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    for c in cmds:
        cmd_name = c["name"]
        sub = subprocess.run([str(launch), cmd_name], check=False, capture_output=True)
        if sub.returncode != 0:
            raise RuntimeError(
                f"`{launch} {cmd_name}` exited {sub.returncode}: can't enumerate schema."
            )
        # Validate it's JSON (rather than store garbage); raises ValueError on bad.
        schema = json.loads(sub.stdout.decode("utf-8"))
        (schemas_dir / f"{cmd_name}.json").write_text(json.dumps(schema))
