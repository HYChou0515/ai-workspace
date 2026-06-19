"""``WorkflowHandle`` (``wf``) — the run's view of its workspace (#100, manual §3).

A thin, async wrapper over the item's ``FileStore``: the orchestration `run()` reads
its inputs and step artifacts, and writes outputs, through this. The filesystem is
the journal (manual §9), so the step engine also reads/writes its ``step_<name>/...``
artifacts through here. Capability methods (ingest, …) and the run-scoped credential
are layered on in later phases; this is the file/IO surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from typing import Any

from ..filestore.protocol import FileStore
from .engine import StepFailed, run_step

# How an agent node runs one turn: given the (feedback-augmented) prompt + the tool
# subset, drive a ChatTurnEngine turn on the item and return a result summary. The
# orchestration driver wires the real implementation (P4); tests inject a fake.
DriveTurn = Callable[[str, list[str] | None], Awaitable[Any]]
# How a deterministic node runs a command in the sandbox, returning (exit_code,
# stdout). Wired by the driver; faked in tests.
RunSandbox = Callable[[str], Awaitable[tuple[int, str]]]
# The ingest capability bound to this run's workspace + captured user (manual §8):
# (collection, path) -> the SourceDoc id. Wired by the driver; faked in tests.
IngestCapability = Callable[[str, str], Awaitable[str]]
# The "did this file land in the collection as ready?" check capability (manual §8):
# (collection, path) -> bool. Wired by the driver; backs ``check.collection_has``.
CollectionChecker = Callable[[str, str], Awaitable[bool]]
# The upsert-context-card capability (manual §8, #111): (collection, keys, title, body)
# -> the card's id. Create-or-update by key (‘有就更新、沒才新增’). Wired by the driver;
# faked in tests.
UpsertCardCapability = Callable[[str, list[str], str, str], Awaitable[str]]


def _card_step_key(keys: list[str], title: str, body: str = "") -> str:
    """A stable, path-safe ``step_card/<key>`` receipt key for one card (manual §8/§9).
    The readable prefix comes from the card's identity (sorted keys, else the title); the
    hash suffix folds in the ``body`` too (#111) so a re-run with the SAME content skips,
    but an edited definition re-fires and upserts the card to the new text rather than
    being masked as already-done. The suffix also keeps the key unique when the prefix
    sanitises to nothing (e.g. CJK-only or symbol keys)."""
    basis = " ".join(sorted(keys)) or title
    safe = re.sub(r"[^0-9a-z]+", "_", basis.casefold()).strip("_")[:48]
    digest = hashlib.sha1(f"{basis}\x00{body}".encode()).hexdigest()[:8]
    return f"{safe}_{digest}" if safe else digest


def _abs(path: str) -> str:
    """Normalise to an absolute workspace-relative path (FileStore wants a leading
    ``/``); accept author-friendly relative paths like ``plan/f.json``."""
    return path if path.startswith("/") else "/" + path


class WorkflowHandle:
    def __init__(
        self,
        *,
        store: FileStore,
        workspace_id: str,
        config: dict[str, Any] | None = None,
        user: str = "",
        drive_turn: DriveTurn | None = None,
        run_sandbox: RunSandbox | None = None,
        emit: Callable[[object], None] | None = None,
        ingest: IngestCapability | None = None,
        collection_checker: CollectionChecker | None = None,
        upsert_card: UpsertCardCapability | None = None,
        credential: str = "",
        step_timeout_s: float | None = None,
    ) -> None:
        self._store = store
        self._workspace_id = workspace_id
        self.config = config or {}
        """The profile's config (manual §20 reads ``wf.config["collections"]``)."""
        self.user = user
        """The captured acting user (manual §15)."""
        self.drive_turn = drive_turn
        """Wired by the orchestration driver — runs one agent turn (manual §5.1)."""
        self.run_sandbox = run_sandbox
        """Wired by the orchestration driver — runs a sandbox command (manual §5.2)."""
        self.emit = emit
        """Wired by the orchestration driver — publishes a phase/step event on the
        item's stream (manual §12). ``None`` ⇒ events are dropped (engine no-op)."""
        self._ingest = ingest
        """Wired by the orchestration driver — the ``ingest_to_collection`` capability
        bound to this run's workspace + captured user (manual §8)."""
        self._collection_has = collection_checker
        """Wired by the orchestration driver — backs ``check.collection_has`` (§8)."""
        self._upsert_card = upsert_card
        """Wired by the orchestration driver — the ``upsert_context_card`` capability
        (create-or-update by key, #111) bound to this run's captured user (manual §8)."""
        self.credential = credential
        """The run-scoped credential (manual §15) — injected into a deterministic
        node's sandbox env so its script can auth capability HTTP calls. "" until
        the orchestrator mints one for the run."""
        self.step_timeout_s = step_timeout_s
        """Per-step wall-clock cap for an agent turn (manual §17); None ⇒ no cap.
        Exceeding it aborts the step (and so the run) to ``error``."""

    async def read(self, path: str) -> bytes:
        return await self._store.read(self._workspace_id, _abs(path))

    async def read_text(self, path: str) -> str:
        return (await self.read(path)).decode()

    async def read_json(self, path: str) -> Any:
        return json.loads(await self.read(path))

    async def write(self, path: str, data: bytes | str) -> None:
        await self._store.write(
            self._workspace_id, _abs(path), data.encode() if isinstance(data, str) else data
        )

    async def write_json(self, path: str, obj: Any) -> None:
        await self.write(path, json.dumps(obj, sort_keys=True).encode())

    async def exists(self, path: str) -> bool:
        return await self._store.exists(self._workspace_id, _abs(path))

    async def delete(self, path: str) -> None:
        await self._store.delete(self._workspace_id, _abs(path))

    async def glob(self, patterns: list[str] | str, exclude: list[str] | None = None) -> list[str]:
        """Workspace files matching any of ``patterns`` (fnmatch), minus any matching
        ``exclude``. A generic primitive — interpreting an ``input.json`` spec into
        these patterns is the App's business (manual §14). Returns absolute paths,
        sorted, so iteration order is deterministic (replay-safe, manual §9)."""
        pats = [patterns] if isinstance(patterns, str) else list(patterns)
        ex = exclude or []
        out = []
        for p in await self._store.ls(self._workspace_id):
            rel = p.lstrip("/")
            if any(fnmatch(rel, pat.lstrip("/")) for pat in pats) and not any(
                fnmatch(rel, e.lstrip("/")) for e in ex
            ):
                out.append(p)
        return sorted(out)

    async def ingest_to_collection(
        self, collection: str, path: str, *, phase: str = "ingest", cache: bool = True
    ) -> str:
        """Deterministic node (manual §8): ingest a workspace file into an existing
        KB collection as the captured user. Journaled + skipped on re-run (§9);
        idempotent (the SourceDoc id is the natural key, so a re-ingest upserts).
        Returns the SourceDoc id."""
        if self._ingest is None:
            raise RuntimeError("ingest_to_collection needs a capability (wired by the run driver)")
        ingest = self._ingest

        async def execute(_feedback: str | None) -> dict[str, str]:
            return {"doc_id": await ingest(collection, path)}

        result = await run_step(
            self,
            name="ingest",
            key=path.lstrip("/").replace("/", "_"),
            phase=phase,
            args={"collection": collection, "path": path},
            execute=execute,
            cache=cache,
        )
        return result["doc_id"]

    async def upsert_context_card(
        self,
        collection: str,
        keys: list[str],
        *,
        title: str = "",
        body: str = "",
        phase: str = "commit",
        cache: bool = True,
    ) -> str:
        """Deterministic node (manual §8, #111): create-or-update a ``ContextCard`` on an
        existing KB collection as the captured user — the ``→collections`` workflow's
        commit of a filled glossary entry. An existing card for the key is overwritten
        (‘有就更新、沒才新增’), so re-classifying the same term doesn't duplicate it.
        Journaled + skipped on re-run (§9); the ``step_card`` receipt key is the card's
        identity, so a re-run with the same content is a no-op. Returns the card id."""
        if self._upsert_card is None:
            raise RuntimeError("upsert_context_card needs a capability (wired by the run driver)")
        upsert_card = self._upsert_card

        async def execute(_feedback: str | None) -> dict[str, str]:
            return {"card_id": await upsert_card(collection, keys, title, body)}

        result = await run_step(
            self,
            name="card",
            key=_card_step_key(keys, title, body),
            phase=phase,
            args={"collection": collection, "keys": list(keys), "title": title},
            execute=execute,
            cache=cache,
        )
        return result["card_id"]

    async def map(
        self,
        fn: Callable[[Any], Awaitable[Any]],
        items: list[Any],
        *,
        concurrency: int = 8,
    ) -> list[dict[str, str]]:
        """The parallel for-each (manual §11): run ``fn(item)`` for every item
        concurrently, bounded by ``concurrency``. A ``StepFailed`` in an element is
        caught and collected (skip+collect) so one bad element doesn't kill the
        batch; returns the ``{item, error}`` failures. NOTE: agent turns on the
        *same* handle still serialize (ChatTurnEngine is FIFO-per-key) — true
        parallel agent turns need per-element sub-handles wired by the driver."""
        sem = asyncio.Semaphore(concurrency)
        failures: list[dict[str, str]] = []

        async def _one(item: Any) -> None:
            async with sem:
                try:
                    await fn(item)
                except StepFailed as exc:
                    failures.append({"item": str(item), "error": str(exc)})

        await asyncio.gather(*(_one(item) for item in items))
        return failures
