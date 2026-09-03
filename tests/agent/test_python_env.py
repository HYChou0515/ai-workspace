"""P1 — a workspace that declares its python dependencies gets them.

`ensure_project_env(sandbox, handle)` runs beside `provision_tools`, after the
snapshot restore and before the agent's first command: if the workspace holds a
`pyproject.toml`, the sandbox's environment is made to match its `uv.lock`.

The design and its rejected alternatives are in
`docs/plan-profile-python-env.md`.
"""

from __future__ import annotations

import pytest

from workspace_app.agent.python_env import ProjectEnvError, ensure_project_env
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle, SandboxSpec


class _Recording:
    """Minimal Sandbox stand-in: records exec argv, answers `exists` from a
    set of paths, returns canned results."""

    def __init__(
        self,
        *,
        present: set[str] | None = None,
        results: dict[tuple[str, ...], ExecResult] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.sinks: list[object] = []
        self._present = present or set()
        self._results = results or {}

    async def create(
        self, spec: SandboxSpec, sandbox_id: str | None = None
    ) -> SandboxHandle:  # pragma: no cover
        return SandboxHandle(id="s1")

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        return path in self._present

    async def exec(self, handle, cmd, on_output=None, env=None) -> ExecResult:
        self.calls.append(cmd)
        self.envs.append(dict(env) if env else {})
        self.sinks.append(on_output)
        return self._results.get(tuple(cmd), ExecResult(exit_code=0, stdout=b"ok"))

    async def kill(self, handle) -> None:  # pragma: no cover
        return None


async def test_a_workspace_that_declares_dependencies_gets_them_synced() -> None:
    """The tracer: a `pyproject.toml` in the workspace means the sandbox is
    brought in line with the lock before the agent runs anything."""
    sb = _Recording(present={"pyproject.toml"})

    await ensure_project_env(sb, SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]

    assert any(c[:2] == ["uv", "sync"] for c in sb.calls), "a declared project must be synced"


async def test_a_workspace_that_declares_nothing_is_left_completely_alone() -> None:
    """Every profile that predates this ships no `pyproject.toml`, and their
    sandboxes must behave exactly as before — not "sync a project that isn't
    there and shrug at the error", but touch nothing at all."""
    sb = _Recording(present=set())

    await ensure_project_env(sb, SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]

    assert sb.calls == [], "an undeclared workspace must not be touched"


async def test_the_lock_decides_not_the_manifest() -> None:
    """`--frozen`: install exactly what the lock says, never re-resolve.

    Without it one lock file can produce different versions on two cold
    starts — which is the whole thing a lock is for. The cost is that a hand
    edit to `pyproject.toml` does not take effect, and THAT has to be said out
    loud rather than left silent; that is a separate behaviour below.
    """
    sb = _Recording(present={"pyproject.toml"})

    await ensure_project_env(sb, SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]

    (sync,) = [c for c in sb.calls if c[:2] == ["uv", "sync"]]
    assert "--frozen" in sync


async def test_an_environment_that_cannot_be_prepared_stops_the_turn() -> None:
    """No degrading. These failures — a package index down, a wheel with no
    build for this platform — are almost always fixable only by whoever runs
    the deployment, and falling back to the image's own interpreter would hand
    the user an environment that looks plausible and is wrong: pandas still
    there, the profile's own dependency missing.

    So it raises, and it carries what an operator needs: uv's own words.
    """
    sb = _Recording(
        present={"pyproject.toml"},
        results={
            ("uv", "sync", "--frozen"): ExecResult(
                exit_code=2,
                stdout=b"error: Distribution `pandas==2.2.3` has no wheel for this platform",
            )
        },
    )

    with pytest.raises(ProjectEnvError) as caught:
        await ensure_project_env(sb, SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]

    assert "no wheel for this platform" in str(caught.value), "uv's own words must survive"


async def test_preparing_the_environment_is_visible_while_it_happens() -> None:
    """This runs inside the agent's first `exec`, before its command. Without
    the output going anywhere the user watches a tool card sit still for as
    long as the install takes, with nothing saying why."""
    seen: list[bytes] = []
    sb = _Recording(present={"pyproject.toml"})

    await ensure_project_env(
        sb,  # ty: ignore[invalid-argument-type]
        SandboxHandle(id="s1"),
        on_output=seen.append,
    )

    (sink,) = [k for c, k in zip(sb.calls, sb.sinks, strict=True) if c[:2] == ["uv", "sync"]]
    assert sink is not None, "the sync's progress must have somewhere to go"


async def test_a_lock_that_no_longer_matches_the_manifest_is_said_out_loud() -> None:
    """`--frozen` means an edit to `pyproject.toml` does not take effect. That
    is the right trade (see the module docstring) but it is SILENT, and silent
    is the failure mode this whole feature is otherwise built to avoid.

    So the staleness is checked and reported — reported, not refused: failing
    here would land on the cold-start path, where the person watching can do
    nothing about it.
    """
    said: list[bytes] = []
    sb = _Recording(
        present={"pyproject.toml"},
        results={("uv", "lock", "--check"): ExecResult(exit_code=1, stdout=b"stale")},
    )

    await ensure_project_env(
        sb,  # ty: ignore[invalid-argument-type]
        SandboxHandle(id="s1"),
        on_output=said.append,
    )

    told = b"".join(said).decode()
    assert "uv add" in told, "the person needs the route, not just the diagnosis"
    assert ["uv", "sync", "--frozen"] in sb.calls, "and the sync still happens"


async def test_a_missing_uv_is_not_reported_as_a_stale_lock() -> None:
    """The staleness check cannot tell "uv says the lock moved on" from "uv did
    not run at all" — a missing binary exits 127 like any other failure.

    Reading every non-zero as staleness told the person the one thing that was
    NOT true: that their `pyproject.toml` had changed. A false diagnosis ahead
    of the real error is worse than no diagnosis, and it is the exact failure
    this feature exists to avoid.

    So the notice comes only after a sync that WORKED, by which point uv
    demonstrably exists.
    """
    said: list[bytes] = []
    sb = _Recording(
        present={"pyproject.toml"},
        results={
            ("uv", "lock", "--check"): ExecResult(exit_code=127, stdout=b"uv: not found"),
            ("uv", "sync", "--frozen"): ExecResult(exit_code=127, stdout=b"uv: not found"),
        },
    )

    with pytest.raises(ProjectEnvError) as caught:
        await ensure_project_env(
            sb,  # ty: ignore[invalid-argument-type]
            SandboxHandle(id="s1"),
            on_output=said.append,
        )

    assert "not found" in str(caught.value), "the real reason must reach the operator"
    assert b"uv.lock" not in b"".join(said), "and must not be preceded by a false one"
