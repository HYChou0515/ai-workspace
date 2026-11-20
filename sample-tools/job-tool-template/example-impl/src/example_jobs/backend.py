"""A DEMO backend: "the other machine" is a folder on this one.

It is wrong for real work and right for seeing the template run end to end.
Replace this package with one that talks to your scheduler; keep its shape.

What it shows a real backend doing:
- the rules for a job's folder are the backend's and are said once, in
  `run_description`: here, `run.sh` is run with `bash`, and `.env` is loaded;
- who may see a job comes from the tool's credential (here a plain variable,
  DEMO_JOBS_USER), and a missing one is `NeedsAction` naming the variable;
- an upload writes at its offset and never truncates; starting an id twice
  starts one job;
- `phase` is one of the template's five words; `state` is this backend's own.

Layout, under DEMO_JOBS_ROOT (default: a folder in the system's temp dir):

    <user>/<job_id>/          the job's folder: what was sent, and what it wrote
    <user>/<job_id>.meta/     this backend's own bookkeeping: pid, exit code, log
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from job_contract import (
    Capacity,
    Entry,
    EnvVar,
    JobBackend,
    NeedsAction,
    NotFound,
    Retryable,
    Status,
    Worker,
)

_LAUNCH = (
    # .env becomes the job's environment; run.sh is the job. The exit code is
    # written last, so its presence means the job ended.
    'set -a; [ -f .env ] && . ./.env; set +a; bash run.sh; echo $? > "$META/exit_code"'
)


class DemoBackend(JobBackend):
    run_description = (
        "DEMO: the job runs on this same machine, in a folder outside the workspace. "
        "Put the command in run.sh at the top of the folder; it runs as `bash run.sh` "
        "inside the folder. A .env file there (KEY=VALUE lines) sets the job's variables. "
        "requirements.txt is NOT installed: the job uses the python already on the "
        "machine. Everything the job writes in its folder can be listed with files and "
        "copied back with fetch. Its output goes to the log."
    )
    resources_schema = {
        "type": "object",
        "properties": {
            "cpus": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
                "description": "Recorded only: the demo runs every job the same way.",
            },
            "priority": {"enum": ["low", "normal", "high"], "default": "normal"},
        },
        "required": ["cpus"],
        "additionalProperties": False,
    }
    env = (
        EnvVar(
            "DEMO_JOBS_USER",
            "Who you are to the demo backend; any name. Each name sees only its own jobs.",
            required=True,
        ),
    )

    # ── where things are ────────────────────────────────────────────────────

    def _home(self) -> Path:
        user = os.environ.get("DEMO_JOBS_USER")
        if not user:
            raise NeedsAction("set DEMO_JOBS_USER: the demo backend lists jobs per name")
        root = os.environ.get("DEMO_JOBS_ROOT") or os.path.join(
            tempfile.gettempdir(), "demo-job-machine"
        )
        return Path(root) / user

    def _folder(self, job_id: str) -> Path:
        return self._home() / job_id

    def _meta(self, job_id: str) -> Path:
        return self._home() / f"{job_id}.meta"

    def _existing(self, job_id: str) -> Path:
        folder = self._folder(job_id)
        if not folder.is_dir():
            raise NotFound(f"no job {job_id!r} for {os.environ['DEMO_JOBS_USER']}")
        return folder

    # ── required ────────────────────────────────────────────────────────────

    def upload(self, job_id: str, path: str, offset: int, data: bytes) -> None:
        target = self._folder(job_id) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write at the offset, never truncate: the same piece twice is harmless.
        fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.pwrite(fd, data, offset)
        finally:
            os.close(fd)

    def start(self, job_id: str, resources: dict[str, Any]) -> None:
        folder = self._existing(job_id)
        meta = self._meta(job_id)
        if not (folder / "run.sh").is_file():
            raise Retryable(
                "the folder has no run.sh at its top: this backend runs `bash run.sh`; "
                "add it and call run again"
            )
        meta.mkdir(exist_ok=True)
        try:
            # Created once, atomically: of two starts at the same moment, one wins
            # and the other starts nothing.
            os.close(os.open(meta / "claimed", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            return
        (meta / "resources.json").write_text(json.dumps(resources))
        with (meta / "output.log").open("ab") as log:
            process = subprocess.Popen(  # noqa: S603 - the demo's whole point is to run it
                ["bash", "-c", _LAUNCH],
                cwd=folder,
                env={**os.environ, "META": str(meta)},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # outlives this tool call; cancel kills its group
            )
        (meta / "started_at").write_text(str(time.time()))
        (meta / "pid").write_text(str(process.pid))

    def status(self, job_id: str) -> Status:
        meta = self._meta(job_id)
        if not (meta / "claimed").exists():
            raise NotFound(f"job {job_id!r} was never started")
        if not (meta / "pid").exists():
            return Status("queued", state="starting")
        if (meta / "cancelled").exists():
            return Status("failed", state="cancelled")
        code = meta / "exit_code"
        if code.exists():
            value = code.read_text().strip()
            if value == "0":
                return Status("succeeded", state="exit 0", worker="this machine")
            return Status("failed", state=f"exit {value}", worker="this machine")
        if _alive(int((meta / "pid").read_text())):
            return Status("running", worker="this machine")
        return Status("failed", state="lost: it stopped without an exit code")

    def list_files(self, job_id: str, path: str) -> list[Entry]:
        folder = self._existing(job_id)
        base = folder / path
        if base.is_file():
            return [Entry(path, is_dir=False, size=base.stat().st_size)]
        entries = []
        for current, dirs, names in os.walk(base):
            here = Path(current)
            for name in sorted(dirs):
                entries.append(Entry((here / name).relative_to(folder).as_posix(), is_dir=True))
            for name in sorted(names):
                full = here / name
                stat = full.stat()
                entries.append(
                    Entry(
                        full.relative_to(folder).as_posix(),
                        is_dir=False,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )
        return entries

    def download(self, job_id: str, path: str, offset: int, limit: int) -> bytes:
        target = self._existing(job_id) / path
        if not target.is_file():
            raise NotFound(f"job {job_id!r} has no file {path!r}")
        with target.open("rb") as stream:
            stream.seek(offset)
            return stream.read(limit)

    def logs(self, job_id: str, offset: int, limit: int) -> tuple[str, int]:
        log = self._meta(job_id) / "output.log"
        if not log.exists():
            self._existing(job_id)
            return "", 0
        text = log.read_text(errors="replace")
        return text[offset : offset + limit], len(text)

    def cancel(self, job_id: str) -> None:
        self._existing(job_id)
        meta = self._meta(job_id)
        if not (meta / "pid").exists() or (meta / "exit_code").exists():
            return  # never started, or already finished: nothing to stop
        (meta / "cancelled").touch()
        try:
            os.killpg(int((meta / "pid").read_text()), signal.SIGTERM)
        except ProcessLookupError:
            pass

    # ── optional ────────────────────────────────────────────────────────────

    def capacity(self) -> Capacity:
        home = self._home()
        running = [
            meta.name.removesuffix(".meta")
            for meta in sorted(home.glob("*.meta"))
            if (meta / "pid").exists()
            and not (meta / "exit_code").exists()
            and _alive(int((meta / "pid").read_text()))
        ]
        worker = Worker(
            "this machine",
            "busy" if running else "idle",
            job_id=running[0] if running else None,
        )
        return Capacity(workers=(worker,))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A finished child of this process that nobody waited for is a zombie:
    # `kill(0)` still succeeds, so ask whether it has exited.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True  # not ours to wait for: the kill above says it exists
    return done == 0
