"""Build a tool package into a self-contained, sandbox-droppable bundle.

The bundle layout (under ``dst/``)::

    .venv/                  uv-built relocatable venv with the package installed
    python/                 a copy of the (portable) cpython the venv was built with
    launch                  tiny shell launcher (handles AT_SECURE dynamic loader)
    commands.json           the package's command-list (cached from `launch`)
    schemas/<cmd>.json      one per command, the metadata + JSON schema
    .built                  marker holding the source content-hash (drives
                            incremental rebuilds — see `_should_rebuild`)

The bundle is fully self-contained (its own python + venv + libs), so the
sandbox needs no uv / network / build step at provision time. ``launch``
forwards to ``python/bin/python <X.Y>`` through the explicit dynamic
loader so glibc's AT_SECURE rules don't strip ``$ORIGIN`` / ``RPATH`` /
``LD_LIBRARY_PATH`` inside the userns chroot jail.

See `docs/plan-skills-and-tools.md` §B.4.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

# Same launcher template as the legacy `scripts/prebuild_tools.py`: the
# AT_SECURE workaround for the jail is necessary, only the surrounding
# orchestration changed. ``{tool}`` becomes the package's bin entrypoint
# (= the [project.scripts] key, i.e. the package name).
_LAUNCH = """\
#!/bin/sh
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$here/.venv/lib/python{ver}/site-packages${{PYTHONPATH:+:$PYTHONPATH}}"
# HOME (caches + any `pip --user` fallback) goes to a per-sandbox dir the exec
# path passes as SANDBOX_HOME — NOT a shared /tmp. The unset fallback is a
# fresh PRIVATE `mktemp -d`, never the shared pod /tmp: forgetting to set it
# degrades to non-persistent, it can never leak to a location other sandboxes
# see (#393). HOME must be writable + outside the workspace (the tool dir may
# be read-only, and ~/.cache would otherwise pollute the synced workspace).
export HOME="${{SANDBOX_HOME:-$(mktemp -d)}}"
export XDG_CACHE_HOME="$HOME/.cache" MPLCONFIGDIR="$HOME/.config/matplotlib"
ld=$(ls /lib64/ld-linux-x86-64.so.2 /lib/ld-linux-aarch64.so.1 2>/dev/null | head -n1)
exec "$ld" "$here/python/bin/python{ver}" "$here/.venv/bin/{tool}" "$@"
"""

# Pure-python launcher for *venv-carrier* packages (e.g. `python-stack`):
# unlike _LAUNCH, there is no `[project.scripts]` entry to dispatch to —
# every argv is forwarded straight to the bundled python with the venv's
# site-packages on PYTHONPATH. The jail bootstrap shims raw `python`
# inside the sandbox to this launcher when the carrier is provisioned,
# so `exec(["python", "script.py"])` lands here with $@ = ("script.py",).
#
# IMPORTANT — `readlink -f "$0"` (not `dirname "$0"`): the jail invokes us
# via a `/tmp/.jailbin/python` symlink, so $0 is the *symlink* path and a
# naive `dirname` lands in `/tmp/.jailbin`, not the carrier's bundle dir.
# The loader then looks for `$here/python/bin/python{ver}` under
# `/tmp/.jailbin/python/bin/...` — since `/tmp/.jailbin/python` is a
# *file* (the symlink target), the lookup fails with ENOTDIR (errno 20):
# `cannot open shared object file: Error 20`. Resolving the symlink
# first puts $here in the real bundle dir.
_PYTHON_LAUNCH = """\
#!/bin/sh
self=$(readlink -f "$0" 2>/dev/null || echo "$0")
here=$(CDPATH= cd -- "$(dirname -- "$self")" && pwd)
export PYTHONPATH="$here/.venv/lib/python{ver}/site-packages${{PYTHONPATH:+:$PYTHONPATH}}"
# HOME (caches + any `pip --user` install fallback) → a per-sandbox dir the exec
# path passes as SANDBOX_HOME. A user's `pip install --break-system-packages X`
# can't write the root-owned carrier venv, so pip defaults to `--user` = $HOME/
# .local; pointing HOME at a per-sandbox dir keeps that install (and matplotlib
# caches) private to this sandbox and reaped with it. The unset fallback is a
# PRIVATE `mktemp -d`, NEVER the shared pod /tmp — so a caller that forgets to
# set SANDBOX_HOME degrades to non-persistent, never leaks across sandboxes
# (#393). In the userns jail the exec path passes SANDBOX_HOME=/tmp explicitly,
# where /tmp is a per-exec ephemeral tmpfs (safe there).
export HOME="${{SANDBOX_HOME:-$(mktemp -d)}}"
export XDG_CACHE_HOME="$HOME/.cache" MPLCONFIGDIR="$HOME/.config/matplotlib"
ld=$(ls /lib64/ld-linux-x86-64.so.2 /lib/ld-linux-aarch64.so.1 2>/dev/null | head -n1)
exec "$ld" "$here/python/bin/python{ver}" "$@"
"""


def build_package(*, name: str, source: Path, dst: Path, force: bool = False) -> None:
    """Build the package at ``source`` into the prebuilt bundle at ``dst``.

    Idempotent: skips when ``source``'s content is unchanged since the
    last successful build (see ``_should_rebuild``). ``force=True``
    rebuilds unconditionally (the ``--force`` prebuild flag).

    Installs deps via ``uv sync --frozen`` against the source's
    committed ``uv.lock`` so every operator's prebuild gets the
    identical pinned versions the package author tested — not whatever
    uv resolves fresh on the host. A source without ``uv.lock`` raises:
    that's a misconfigured package (no way to honour "pinned deps" if
    there are none).

    #64: the local package is reinstalled with ``--reinstall-package`` +
    ``--refresh-package`` so a *same-version* source edit actually lands.
    uv caches built wheels keyed by (name, version); without forcing a
    refresh, ``uv sync`` reinstalls the stale cached wheel and the edit
    is silently ignored until the version is bumped.
    """
    lockfile = source / "uv.lock"
    if not lockfile.is_file():
        raise RuntimeError(
            f"package source {source} has no uv.lock — prebuild needs "
            f"a frozen lockfile so bundled deps are reproducible. Run "
            f"`uv lock` in {source} and commit the result."
        )
    if not force and not _should_rebuild(source, dst):
        return
    # The distribution name uv knows the local package by (== the
    # [project.scripts] entrypoint for CLI packages). Used to target the
    # cache-busting reinstall/refresh at just this package.
    project = tomllib.loads((source / "pyproject.toml").read_text()).get("project", {})
    dist_name = project.get("name") or name
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    venv = dst / ".venv"
    subprocess.run(["uv", "venv", "--relocatable", str(venv)], check=True)
    # `uv sync --frozen` installs deps from the lockfile into the
    # source's `.venv` by default; UV_PROJECT_ENVIRONMENT redirects
    # that to our bundle's venv. `--no-editable` makes the project
    # itself install as a real copy (not a symlink back to source) so
    # the bundle is self-contained and survives source moves.
    # `--reinstall-package`/`--refresh-package` defeat uv's version-keyed
    # wheel cache for the local package so edited-but-same-version source
    # is rebuilt from disk (#64), not restored stale from cache.
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv.resolve())}
    subprocess.run(
        [
            "uv",
            "sync",
            "--frozen",
            "--no-editable",
            "--reinstall-package",
            dist_name,
            "--refresh-package",
            dist_name,
            "--directory",
            str(source),
        ],
        check=True,
        env=env,
    )
    # Bundle the (portable) python the venv was built against, and derive its X.Y.
    real = (venv / "bin" / "python").resolve()  # .../cpython-X.Y.Z/bin/pythonX.Y
    ver = real.name.removeprefix("python")  # "3.13"
    shutil.copytree(real.parent.parent, dst / "python", symlinks=True)
    launch = dst / "launch"
    if _is_venv_carrier(source):
        # No commands to dispatch: write the pure-python launcher and
        # leave a *valid-but-empty* commands.json + schemas/ so the
        # discover walker accepts the shape (see registry.py).
        launch.write_text(_PYTHON_LAUNCH.format(ver=ver))
        launch.chmod(0o755)
        (dst / "commands.json").write_text("[]")
        (dst / "schemas").mkdir()
    else:
        launch.write_text(_LAUNCH.format(ver=ver, tool=name))
        launch.chmod(0o755)
        # Sandbox-side `launch` is invoked through bash + a custom loader;
        # here at build time we want the host's venv-bin script directly
        # so we can actually run it without the jail's AT_SECURE setup.
        # Use the venv's console_script entrypoint as the "launch" for
        # schema-dumping.
        _dump_schemas(venv / "bin" / name, dst)
    # Record the build stamp (source content-hash + launcher-template
    # fingerprint) so the next prebuild can tell whether anything actually
    # changed (mtime is unreliable; see _should_rebuild).
    (dst / ".built").write_text(_build_stamp(source))


# uv-run debug launchers (#63): no bundled python/venv. The package source is
# symlinked into the bundle as `project`, and the launch runs it via
# `uv run --project "$here/project"` — crucially `--project`, NOT `--directory`:
# --directory would `cd` into the source dir, so a tool's workspace-relative
# write (`./step2-data/...`) would land in the source tree instead of the
# sandbox workspace. --project keeps the caller's cwd (the workspace), so the
# sandbox boundary is respected. `$here` resolves the launch's own dir, so the
# symlink is reached the same way whether invoked by abspath (build-time schema
# dump) or via the sandbox's /.tools mount. `unset VIRTUAL_ENV` silences uv's
# "VIRTUAL_ENV does not match" warning (the app's venv leaks into the env).
_UVRUN_LAUNCH = """\
#!/bin/sh
unset VIRTUAL_ENV
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec uv run --project "$here/project" {tool} "$@"
"""

_UVRUN_CARRIER_LAUNCH = """\
#!/bin/sh
unset VIRTUAL_ENV
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec uv run --project "$here/project" python "$@"
"""


def build_package_uvrun(*, name: str, source: Path, dst: Path) -> None:
    """Build a lightweight DEBUG bundle (#63) for ``source`` into ``dst``:
    a ``project`` symlink → the live source, a ``launch`` that runs it via
    ``uv run --project`` (preserving the sandbox's cwd), plus the cached
    ``commands.json`` / ``schemas/`` the discover walker needs. No copied
    python or venv, so a source edit is picked up on the next call with no
    rebuild. Requires uv on the host + a non-isolated sandbox at run time."""
    src_abs = source.resolve()
    project = tomllib.loads((source / "pyproject.toml").read_text()).get("project", {})
    is_carrier = not project.get("scripts")
    dst.mkdir(parents=True, exist_ok=True)
    # Softlink the live source in — the sandbox reaches it through this link
    # (no copy), and the launch's `--project "$here/project"` points at it.
    link = dst / "project"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(src_abs)
    launch = dst / "launch"
    if is_carrier:
        # Venv carrier (e.g. python-stack): no CLI to dispatch — `uv run python`
        # gives the agent the carrier's deps. Empty commands.json/schemas keep
        # discover_packages happy (same shape as the prebuilt carrier).
        launch.write_text(_UVRUN_CARRIER_LAUNCH)
        launch.chmod(0o755)
        (dst / "commands.json").write_text("[]")
        (dst / "schemas").mkdir(exist_ok=True)
    else:
        launch.write_text(_UVRUN_LAUNCH.format(tool=name))
        launch.chmod(0o755)
        _dump_schemas(launch, dst)


def provision_uvrun(packages: dict[str, Path], dst_root: Path) -> Path:
    """Build uv-run debug bundles for every existing package source under
    ``dst_root/<name>`` and return ``dst_root`` (the tools dir). Missing
    source dirs are skipped (mirrors the prebuild script — keeps the
    gitignored in-house ``rca-tools`` optional)."""
    dst_root.mkdir(parents=True, exist_ok=True)
    for name, source in packages.items():
        if not source.is_dir():
            continue
        build_package_uvrun(name=name, source=source, dst=dst_root / name)
    return dst_root


def _is_venv_carrier(source: Path) -> bool:
    """True iff the source package declares no `[project.scripts]` table.

    Such a package is a *venv carrier*: its prebuild bundle ships the
    uv-locked venv but no CLI dispatcher. The sandbox's jail bootstrap
    routes raw `python` calls to its launcher when present, so the agent
    can run plain `exec(["python", "script.py"])` with the carrier's
    dependencies imported.

    Packages WITH a scripts table follow the standard 3-stage contract.
    """
    pyproject = source / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    return not data.get("project", {}).get("scripts")


def _source_hash(source: Path) -> str:
    """A content digest of every author-edited file under ``source``.

    Dot-prefixed paths (`.venv`, `.git`, a leftover build `.venv`) are
    excluded — they aren't inputs. The digest folds in each file's
    relative path AND bytes, so renaming, adding, deleting, or editing
    any tracked file changes it. Editing content WITHOUT moving the
    mtime (the exact shape of #64) still flips the hash, so a rebuild
    fires where the old mtime check silently skipped."""
    h = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if not path.is_file():
            continue
        h.update(rel.as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _builder_fingerprint() -> str:
    """A digest of the launch templates baked INTO each bundle at build time.

    Folded into the ``.built`` stamp so that editing a launcher template
    (`_LAUNCH` / `_PYTHON_LAUNCH`) forces a rebuild of already-built bundles.
    `_source_hash(source)` cannot see such an edit — the template is builder
    code, not package source, yet it is written into the bundle's `launch`.
    Without this, changing `_PYTHON_LAUNCH` (e.g. #393's `SANDBOX_HOME`
    routing) would silently keep the stale `launch` in every cached bundle,
    the same stale-cache class as #64's version-keyed wheel."""
    h = hashlib.sha256()
    for tpl in (_LAUNCH, _PYTHON_LAUNCH):
        h.update(tpl.encode())
        h.update(b"\0")
    return h.hexdigest()


def _build_stamp(source: Path) -> str:
    """The value recorded in ``dst/.built``: the source content hash AND the
    builder fingerprint. A rebuild fires when EITHER changes."""
    return f"{_source_hash(source)}\n{_builder_fingerprint()}"


def _should_rebuild(source: Path, dst: Path) -> bool:
    """True iff ``dst`` is missing its build marker or its recorded stamp
    differs from the current one — i.e. the ``source`` content OR the launch
    templates baked into the bundle changed since the last build.

    Content-hash, not mtime (#64): uv caches built wheels keyed by
    version, so a same-version edit could land the stale wheel; the old
    mtime check made it worse by not even noticing edits whose mtime
    didn't advance. Hashing the tree triggers a rebuild on any content
    change. The build itself then forces uv to refresh the local wheel.
    The builder fingerprint (#393) extends this to launcher-template edits."""
    marker = dst / ".built"
    if not marker.exists():
        return True
    return marker.read_text().strip() != _build_stamp(source).strip()


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
