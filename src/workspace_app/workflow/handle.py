"""``WorkflowHandle`` (``wf``) — the run's view of its workspace (#100, manual §3).

A thin, async wrapper over the item's ``FileStore``: the orchestration `run()` reads
its inputs and step artifacts, and writes outputs, through this. The filesystem is
the journal (manual §9), so the step engine also reads/writes its ``step_<name>/...``
artifacts through here — under the run's per-workflow journal folder ``journal_dir``
(``/.workflow/<workflow_id>``, #136), so the journal stays out of the workspace root.
Capability methods (ingest, …) and the run-scoped credential are layered on in later
phases; this is the file/IO surface.
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
# A live-stdout sink (#178): called with each stdout byte chunk as it arrives, so a
# long deterministic step shows movement instead of looking dead. Matches the sandbox
# protocol's OutputSink (defined here to keep the workflow package decoupled).
OutputSink = Callable[[bytes], None]
# How a deterministic node runs a command in the sandbox, returning (exit_code,
# stdout). ``on_output`` streams stdout chunks live (#178); None ⇒ no streaming.
# Wired by the driver; faked in tests.
RunSandbox = Callable[[str, "OutputSink | None"], Awaitable[tuple[int, str]]]
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
# The find-overwrite-target capability (#205): (collection, keys, title) -> the EXISTING
# card a commit-time upsert would overwrite, as {keys, title, body, ambiguity}, or None for
# a new card. Read-only — backs the review "before" snapshot. Wired by the driver; faked
# in tests.
FindCardCapability = Callable[[str, list[str], str], Awaitable[dict[str, Any] | None]]
# The convert capability (#324): (src, dest) -> (out_path, kind). Reads the staged upload at
# ``src``, runs the KB parsers to text, stages the converted artifact at a content-coherent
# path, and returns where the caller files it (``None`` for an unreadable binary → skip).
# Wired by the driver; faked in tests.
ConvertCapability = Callable[[str, str], Awaitable[tuple[str | None, str]]]


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
        workflow_id: str = "",
        config: dict[str, Any] | None = None,
        upload_dir: str = "uploads",
        user: str = "",
        drive_turn: DriveTurn | None = None,
        run_sandbox: RunSandbox | None = None,
        emit: Callable[[object], None] | None = None,
        ingest: IngestCapability | None = None,
        convert: ConvertCapability | None = None,
        collection_checker: CollectionChecker | None = None,
        upsert_card: UpsertCardCapability | None = None,
        find_card: FindCardCapability | None = None,
        credential: str = "",
        step_timeout_s: float | None = None,
    ) -> None:
        self._store = store
        self._workspace_id = workspace_id
        self._workflow_id = workflow_id
        """Which of the profile's workflows this run executes (manual §4). Scopes the
        journal directory (#136) so each workflow's ``step_*`` artifacts live under
        their own folder instead of scattered at the workspace root."""
        self.config = config or {}
        """The profile's config (manual §20 reads ``wf.config["collections"]``)."""
        self.upload_dir = upload_dir
        """#198: the profile's staging folder — where a chat attach lands and what a
        workflow globs for dropped files (``{upload_dir}/*``). Injected from the active
        profile's ``ProfileManifest.upload_dir`` so the attach landing and the glob never
        drift (replaces the hardcoded ``uploads/`` of #234). Defaults to ``uploads``."""
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
        self._convert = convert
        """Wired by the orchestration driver — the ``convert_upload`` capability (#324)
        bound to this run's workspace; converts a staged upload to text before filing."""
        self._collection_has = collection_checker
        """Wired by the orchestration driver — backs ``check.collection_has`` (§8)."""
        self._upsert_card = upsert_card
        """Wired by the orchestration driver — the ``upsert_context_card`` capability
        (create-or-update by key, #111) bound to this run's captured user (manual §8)."""
        self._find_card = find_card
        """Wired by the orchestration driver — the read-only ``find_overwrite_target``
        capability (#205) backing the review "before" snapshot. None ⇒ no existing card
        is ever found (a fresh workspace), so the snapshot is empty."""
        self.credential = credential
        """The run-scoped credential (manual §15) — injected into a deterministic
        node's sandbox env so its script can auth capability HTTP calls. "" until
        the orchestrator mints one for the run."""
        self.step_timeout_s = step_timeout_s
        """Per-step wall-clock cap for an agent turn (manual §17); None ⇒ no cap.
        Exceeding it aborts the step (and so the run) to ``error``."""

    @property
    def journal_dir(self) -> str:
        """The run's journal home (#136): ``/.workflow/<workflow_id>`` — the folder
        every ``step_<name>/<key>`` artifact lives under, so the journal stops
        cluttering the workspace root. Legacy singular workflows (``workflow_id=""``)
        fall back to ``/.workflow/_default``."""
        return f"/.workflow/{self._workflow_id or '_default'}"

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

    async def create_entity(
        self,
        type_name: str,
        args: dict[str, Any],
        *,
        name: str,
        on_duplicate: str = "update",
        phase: str = "commit",
        key: str = "",
        cache: bool = True,
    ) -> int:
        """Create a file-first entity (#419) through the framework numbering + validation
        pipeline (the SAME ``EntityStore`` path the UI and agent use, never a raw
        ``wf.write``), as a non-idempotent capability on the #435 shell.

        ``name`` is the capability's STABLE dedup identity — its *site*. A re-run of the
        same site self-dedups: journal-first via a write-once ``created.json`` remembers
        the number this site minted, so a gate revise that CHANGES the content merges
        into that entity instead of minting a duplicate (the old ``args``-digest key
        double-created here — the changed content changed the key). ``on_duplicate`` picks
        the duplicate action: ``update`` (self-merge the declared fields, leaving fields
        the workflow doesn't declare — a human's edits — untouched, 决议3) or ``skip``
        (return the existing number without touching it). ``key`` is the map-element scope
        key (a create_entity inside a loop mints one entity per element). Returns the
        entity number (sourced from the shell's published ``Result.fields['number']``)."""
        from datetime import UTC, datetime

        from ..entity.catalog import discover_catalog
        from ..entity.store import EntityConflict, EntityStore
        from .nonidempotent import Result, Verdict, run_nonidempotent

        catalog, _diags = await discover_catalog(self._store, self._workspace_id)
        if type_name not in catalog:
            raise StepFailed(f"unknown entity type: {type_name!r}")
        store = EntityStore(self._store, self._workspace_id, catalog)
        records_path = catalog.get(type_name).records_path
        created_path = f"{self.journal_dir}/step_{name}/{key or 'main'}.created.json"

        async def _remembered_alive() -> int | None:
            """The number this site already minted, if its record still exists — else
            the entity was hard-deleted, so forget it and mint anew."""
            if not await self.exists(created_path):
                return None
            number = int((await self.read_json(created_path))["number"])
            alive = await self._store.exists(self._workspace_id, f"/{records_path}/{number}.md")
            return number if alive else None

        async def decide(_feedback: str | None) -> Verdict:
            remembered = await _remembered_alive()  # journal-first self-dedup (决议2)
            if remembered is not None:
                return Verdict(kind="duplicate", payload={"of": remembered, "origin": "self"})
            return Verdict(kind="new")

        async def act(verdict: Verdict) -> Result:
            if verdict.kind == "duplicate":
                number = int(verdict.payload["of"])
                if on_duplicate == "skip":
                    return Result(fields={"number": number, "created": False, "action": "skip"})
                current = await store.get(type_name, number)
                try:  # self-merge overlays the declared fields; CAS guards a racing edit
                    await store.update(
                        type_name, number, dict(args), expected_version=current.version
                    )
                except EntityConflict as exc:
                    raise StepFailed(str(exc)) from exc
                return Result(fields={"number": number, "created": False, "action": "merge"})
            # new — guard the act-crash-before-journal window (§7): if this site already
            # minted a (still-alive) entity, reuse it rather than mint a second.
            remembered = await _remembered_alive()
            if remembered is not None:
                return Result(fields={"number": remembered, "created": False, "action": "resume"})
            created = await store.create(
                type_name, args, actor=self.user, now=datetime.now(UTC).date().isoformat()
            )
            await self.write_json(created_path, {"number": created.number})
            return Result(fields={"number": created.number, "created": True, "action": "create"})

        result = await run_nonidempotent(
            self,
            name=name,
            inputs={"type": type_name, "args": args, "on_duplicate": on_duplicate},
            decide=decide,
            act=act,
            key=key,
            phase=phase,
            cache=cache,
        )
        return int(result.fields["number"])

    async def convert(
        self, src: str, dest: str, *, phase: str = "convert", cache: bool = True
    ) -> tuple[str | None, str]:
        """Deterministic node (#324): convert a staged upload at ``src`` to text and stage
        it at a content-coherent path derived from ``dest``, BEFORE it is filed into a
        collection — so only the converted artifact is stored, never the raw binary.
        Journaled + skipped on re-run (§9) so a (VLM) conversion never re-runs. Returns
        ``(out_path, kind)`` — the bare workspace path the caller files next, or
        ``(None, "none")`` when no parser could read the upload (caller skips it)."""
        if self._convert is None:
            raise RuntimeError("convert needs a capability (wired by the run driver)")
        convert = self._convert

        async def execute(_feedback: str | None) -> dict[str, Any]:
            out_path, kind = await convert(src, dest)
            return {"out_path": out_path, "kind": kind}

        result = await run_step(
            self,
            name="convert",
            key=src.lstrip("/").replace("/", "_"),
            phase=phase,
            args={"src": src, "dest": dest},
            execute=execute,
            cache=cache,
        )
        return result["out_path"], result["kind"]

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

    async def find_overwrite_card(
        self, collection: str, keys: list[str], *, title: str = ""
    ) -> dict[str, Any] | None:
        """#205: the EXISTING card a commit-time ``upsert_context_card(collection, keys,
        title)`` would overwrite — as ``{keys, title, body, ambiguity}`` — or ``None`` when
        it would create a new card. Read-only; the ``→collections`` review uses it to write
        the diff "before" snapshot (``.readonly/context-card.current.md``). No journaling
        (a pure read inside the deterministic assemble step). ``None`` capability (an
        unwired handle) ⇒ ``None`` (no card found), so a fresh workspace diffs as all-new."""
        if self._find_card is None:
            return None
        return await self._find_card(collection, keys, title)

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
