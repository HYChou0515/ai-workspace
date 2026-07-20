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
import copy
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

from ..entity.events import EntityOrigin, EntityWriteSink
from ..filestore.protocol import FileStore
from .engine import StepFailed, input_hash, run_step
from .nonidempotent import Result, Verdict, run_nonidempotent

logger = logging.getLogger(__name__)

# How an agent node runs one turn: given the (feedback-augmented) prompt + the tool
# subset, drive a ChatTurnEngine turn on the item and return a result summary. The
# orchestration driver wires the real implementation (P4); tests inject a fake.
DriveTurn = Callable[[str, list[str] | None], Awaitable[Any]]
# A per-element turn-lane factory (#429 P5): subkey -> a DriveTurn bound to a DISTINCT
# ChatTurnEngine key, so N map elements' agent turns run concurrently instead of
# serializing behind one FIFO-per-key lane. Wired by the driver; None ⇒ sub-handles
# share the parent's lane (serialized, the safe default).
SubTurn = Callable[[str], DriveTurn]
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
# (collection, keys, title, body, reference_doc_ids) → card id. The last arg is #518's
# tri-state link list: None ⇒ leave the card's existing links alone.
UpsertCardCapability = Callable[[str, list[str], str, str, list[str] | None], Awaitable[str]]
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
# The one-shot LLM classifier backing ``create_entity``'s M1-AI cross-origin dedup (#435
# P3): prompt -> the model's text answer. Wired by the driver over the workflow's ILlm
# (streamed under the hood); tests inject a fake. ``None`` ⇒ no cross-origin AI dedup
# (journal-first self-dedup still works) — so it is inert until wired + live-checked (P6).
AskLlm = Callable[[str], Awaitable[str]]
# The send-notification capability (#435 P5): (recipient, title, body, dedup_key) -> the
# notification id. Creates one in-app Notification carrying the send-once fingerprint —
# the create IS both the send and the ledger entry (M1, atomic). Wired by the driver over
# the Notification store; faked in tests.
NotifyCapability = Callable[[str, str, str, str], Awaitable[str]]
# The send-ledger query (#435 P5): (dedup_key) -> has this fingerprint already been sent?
# An indexed Notification query — the store IS the ledger. Wired by the driver; faked in
# tests. ``None`` ⇒ never deduped (every send fires).
NotificationSentCheck = Callable[[str], Awaitable[bool]]


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
        ask_llm: AskLlm | None = None,
        notify: NotifyCapability | None = None,
        notification_sent: NotificationSentCheck | None = None,
        credential: str = "",
        step_timeout_s: float | None = None,
        sub_turn: SubTurn | None = None,
        turn_concurrency: int | None = None,
        entity_write_sink: EntityWriteSink | None = None,
        origin_trigger: str = "",
        trigger_depth: int = 0,
        run_id: str = "",
        run_started_at: datetime | None = None,
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
        self.ask_llm = ask_llm
        """Wired by the orchestration driver — the one-shot LLM classifier backing
        ``create_entity``'s M1-AI cross-origin dedup (#435 P3). None ⇒ no cross-origin AI
        match (journal-first self-dedup still works)."""
        self._notify = notify
        """Wired by the orchestration driver — the ``send_notification`` capability (#435
        P5) over the in-app Notification store."""
        self._notification_sent = notification_sent
        """Wired by the orchestration driver — the send-ledger query backing
        ``send_notification``'s M1-fingerprint dedup (#435 P5)."""
        self.credential = credential
        """The run-scoped credential (manual §15) — injected into a deterministic
        node's sandbox env so its script can auth capability HTTP calls. "" until
        the orchestrator mints one for the run."""
        self.step_timeout_s = step_timeout_s
        """Per-step wall-clock cap for an agent turn (manual §17); None ⇒ no cap.
        Exceeding it aborts the step (and so the run) to ``error``."""
        self.sub_turn = sub_turn
        """#429 P5: a ``subkey → DriveTurn`` factory the driver wires so ``sub_handle``
        can bind each map element its own turn lane (real parallel agent turns). None ⇒
        sub-handles reuse the parent lane (serialized — the safe default / tests)."""
        self.turn_concurrency = turn_concurrency
        """#429 P5: the effective parallel-turn ceiling derived from the model backend's
        concurrency (a single local model → ~1, a hosted/multi-replica pool → larger). It
        is a REQUEST ceiling throttled by the backend, not a guarantee. None ⇒ unset (the
        author's per-map ``concurrency`` stands alone)."""
        self._entity_write_sink = entity_write_sink
        """#429 P9: the post-commit sink this run's entity writes emit through (the event-
        trigger dispatcher). None ⇒ no event dispatch (tests / triggers off)."""
        self._origin_trigger = origin_trigger
        """#429 P9: the event trigger that spawned this run (or "" for human/schedule). Stamped
        onto this run's entity writes so the dispatcher never re-fires the run's OWN trigger."""
        self._trigger_depth = trigger_depth
        """#429 P9: this run's depth in the event-trigger chain — stamped onto its writes so an
        indirect cycle hits the global depth cap."""
        self.run_id = run_id
        """#435 P7: this run's per-invocation identity (the ``WorkflowRun`` resource_id —
        stable across a resume, DISTINCT per separate firing). ``create_new`` folds it into
        its dedup key so each separate invocation mints a FRESH entity while a resume of the
        same invocation reuses. "" ⇒ no per-invocation boundary (tests / legacy), so
        ``create_new`` degrades to within-invocation self-dedup only."""
        self.run_started_at = run_started_at
        """#435 P7: this run's creation instant (specstar ``created_time`` — resume-stable,
        unlike the driver's ``started`` which a resume overwrites). ``send_notification``'s
        per-window fingerprint buckets it (once-per-window rather than once-ever). None ⇒ no
        window source → the fingerprint stays once-ever."""

    @property
    def journal_dir(self) -> str:
        """The run's journal home (#136): ``/.workflow/<workflow_id>`` — the folder
        every ``step_<name>/<key>`` artifact lives under, so the journal stops
        cluttering the workspace root. Legacy singular workflows (``workflow_id=""``)
        fall back to ``/.workflow/_default``."""
        return f"/.workflow/{self._workflow_id or '_default'}"

    def sub_handle(self, subkey: str) -> WorkflowHandle:
        """A per-element child handle (#429 P5) sharing this run's workspace, journal, and
        capabilities, but whose agent turns run on a DISTINCT turn lane — so N map elements'
        turns run concurrently instead of serializing behind one ChatTurnEngine key. The
        driver wires ``sub_turn`` (a ``subkey → DriveTurn`` factory); without it the child
        reuses the parent's ``drive_turn`` (graceful degrade to serialized). Everything else
        (store, ``journal_dir``, capabilities, ``emit``) is shared, so per-element artifacts
        land in the same journal keyed by the element key."""
        child = copy.copy(self)
        if self.sub_turn is not None:
            child.drive_turn = self.sub_turn(subkey)
        return child

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
        self,
        collection: str,
        path: str,
        *,
        phase: str = "ingest",
        cache: bool = True,
        name: str = "",
        key: str = "",
    ) -> str:
        """Deterministic node (manual §8): ingest a workspace file into an existing
        KB collection as the captured user. Journaled + skipped on re-run (§9);
        idempotent (the SourceDoc id is the natural key, so a re-ingest upserts).
        Returns the SourceDoc id.

        #518: passing ``name`` publishes the created id as ``{steps.<name>.doc_id}``.
        Doing so changes WHERE the receipt lives (``step_<name>/<scope key>`` with a
        ``fields`` payload, the shape ``_lookup_step`` reads) — which is also this
        step's skip-on-re-run identity. Anonymous calls keep the original
        ``step_ingest/<path>`` receipt byte-for-byte, so a workflow written before this
        existed still skips the ingests it already did instead of redoing them."""
        if self._ingest is None:
            raise RuntimeError("ingest_to_collection needs a capability (wired by the run driver)")
        ingest = self._ingest

        async def execute(_feedback: str | None) -> dict[str, Any]:
            doc_id = await ingest(collection, path)
            return {"fields": {"doc_id": doc_id}} if name else {"doc_id": doc_id}

        result = await run_step(
            self,
            name=name or "ingest",
            key=key if name else path.lstrip("/").replace("/", "_"),
            phase=phase,
            args={"collection": collection, "path": path},
            execute=execute,
            cache=cache,
        )
        return result["fields"]["doc_id"] if name else result["doc_id"]

    @property
    def entity_origin(self) -> EntityOrigin | None:
        """#429 P9/P10: the ``EntityOrigin`` to stamp on this run's entity writes — set only for
        a triggered run (``origin_trigger`` non-empty), so a human/schedule run's writes stay
        origin-less (a fresh depth-0 chain root). One source of truth: this run's own capability
        writes stamp it directly, and ``WorkflowExecutor.wire_handle`` reads it to give an agent
        node's turn the same origin — so an agent-mediated write is depth-counted like any other,
        keeping the dispatcher's self-trigger + depth-cap guards effective on the agent path."""
        if not self._origin_trigger:
            return None
        return EntityOrigin(trigger=self._origin_trigger, depth=self._trigger_depth)

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
        from .entity_dedup import (
            match_prompt,
            parse_match,
            render_contribution,
            replace_fenced_block,
        )

        catalog, _diags = await discover_catalog(self._store, self._workspace_id)
        if type_name not in catalog:
            raise StepFailed(f"unknown entity type: {type_name!r}")
        store = EntityStore(
            self._store, self._workspace_id, catalog, on_write=self._entity_write_sink
        )
        records_path = catalog.get(type_name).records_path
        # create_new (M2, 决议4): the dedup identity is per-INVOCATION. The created.json path
        # is workflow_id-scoped, which a manual re-run reuses (→ silently reusing the entity);
        # folding this run's token makes each SEPARATE invocation mint fresh while a resume
        # (same run_id) still finds its created.json and reuses. "" (no run_id / not create_new)
        # ⇒ the original workflow-scoped path (within-invocation self-dedup only).
        token = self.run_id if on_duplicate == "create_new" else ""
        token_suffix = f".{token}" if token else ""
        created_path = f"{self.journal_dir}/step_{name}/{key or 'main'}{token_suffix}.created.json"

        async def _remembered_alive() -> int | None:
            """The number this site already minted, if its record still exists — else
            the entity was hard-deleted, so forget it and mint anew."""
            if not await self.exists(created_path):
                return None
            number = int((await self.read_json(created_path))["number"])
            alive = await self._store.exists(self._workspace_id, f"/{records_path}/{number}.md")
            return number if alive else None

        async def _cross_match() -> int | None:
            """M1-AI cross-origin dedup (决议2/8): does this new entity correspond to one
            an OTHER origin already filed? Reversible act only (a non-destructive enrich),
            so fail-open — any AI failure/hallucination → NEW (``None``). Inert when no
            LLM is wired."""
            if self.ask_llm is None:
                return None
            existing = (await store.query(type_name)).entities
            candidates = [
                {"number": e.number, "title": e.fields.get("title", "")} for e in existing
            ]
            if not candidates:
                return None
            try:
                answer = await self.ask_llm(match_prompt(dict(args), candidates))
            except Exception:  # noqa: BLE001 — fail-open (决议8): any AI failure → NEW
                logger.warning(
                    "create_entity %s: cross-match LLM failed, fail-open to NEW",
                    type_name,
                    exc_info=True,
                )
                return None
            return parse_match(answer, [c["number"] for c in candidates])

        async def decide(_feedback: str | None) -> Verdict:
            remembered = await _remembered_alive()  # journal-first self-dedup (决议2)
            if remembered is not None:
                return Verdict(kind="duplicate", payload={"of": remembered, "origin": "self"})
            # create_new (M2, 决议4): a fresh entity per invocation — it must NOT dedup
            # against another origin's entity, so skip M1-AI cross-matching. The per-run
            # created.json (above) + the run token folded into the shell input hash (below)
            # make each SEPARATE invocation mint fresh while a resume/revise of THIS one
            # reuses. The ruling is the reserved ``token`` kind (M2 exactly-once, carrying
            # run_id) — the per-invocation idempotency key the shell reserved (P7).
            if on_duplicate == "create_new":
                return Verdict(kind="token", payload={"token": token})
            matched = await _cross_match()  # M1-AI cross-origin (决议2, P3)
            if matched is not None:
                return Verdict(kind="duplicate", payload={"of": matched, "origin": "cross"})
            return Verdict(kind="new")

        async def _cross_merge(number: int) -> None:
            """Enrich an OTHER origin's entity non-destructively (决议3/5): fill only empty
            frontmatter fields (never overwrite a human's non-empty value) and overwrite
            the workflow-owned fenced block in the body (idempotent — no accumulation)."""
            current = await store.get(type_name, number)
            patch = {k: v for k, v in args.items() if not current.fields.get(k)}
            new_body = replace_fenced_block(
                current.body, name, render_contribution(name, dict(args))
            )
            try:
                await store.update(
                    type_name,
                    number,
                    patch,
                    expected_version=current.version,
                    body=new_body,
                    actor=self.user,
                    origin=self.entity_origin,
                )
            except EntityConflict as exc:
                raise StepFailed(str(exc)) from exc

        async def act(verdict: Verdict) -> Result:
            if verdict.kind == "duplicate":
                number = int(verdict.payload["of"])
                if on_duplicate == "skip":
                    return Result(fields={"number": number, "created": False, "action": "skip"})
                if verdict.payload.get("origin") == "cross":  # someone else's entity — enrich
                    await _cross_merge(number)
                    return Result(
                        fields={"number": number, "created": False, "action": "cross-merge"}
                    )
                current = await store.get(type_name, number)
                try:  # self-merge overlays the declared fields; CAS guards a racing edit
                    await store.update(
                        type_name,
                        number,
                        dict(args),
                        expected_version=current.version,
                        actor=self.user,
                        origin=self.entity_origin,
                    )
                except EntityConflict as exc:
                    raise StepFailed(str(exc)) from exc
                return Result(fields={"number": number, "created": False, "action": "merge"})
            # new / token — guard the act-crash-before-journal window (§7): if this site
            # already minted a (still-alive) entity, reuse it rather than mint a second. For
            # ``token`` (create_new) ``_remembered_alive`` reads the PER-RUN created.json, so
            # this reuses only within the same invocation, never across separate runs.
            remembered = await _remembered_alive()
            if remembered is not None:
                return Result(fields={"number": remembered, "created": False, "action": "resume"})
            created = await store.create(
                type_name,
                args,
                actor=self.user,
                now=datetime.now(UTC).date().isoformat(),
                origin=self.entity_origin,
            )
            await self.write_json(created_path, {"number": created.number})
            logger.info("create_entity: created %s #%s", type_name, created.number)
            return Result(fields={"number": created.number, "created": True, "action": "create"})

        inputs: dict[str, Any] = {"type": type_name, "args": args, "on_duplicate": on_duplicate}
        if token:
            # Fold the per-invocation token into the shell input hash so a NEW invocation (the
            # journal is workflow_id-scoped, else re-used) re-runs decide/act and re-mints
            # instead of returning the cached prior-run result.
            inputs["token"] = token
        result = await run_nonidempotent(
            self,
            name=name,
            inputs=inputs,
            decide=decide,
            act=act,
            key=key,
            phase=phase,
            cache=cache,
        )
        return int(result.fields["number"])

    async def send_notification(
        self,
        recipient: str,
        topic: str,
        *,
        name: str,
        title: str = "",
        body: str = "",
        window: str = "",
        phase: str = "notify",
        key: str = "",
        cache: bool = True,
    ) -> dict[str, Any]:
        """Send one in-app notification as a non-idempotent capability on the #435 shell
        (M1 send-once). ``decide`` queries the Notification store by the send fingerprint —
        the store IS the ledger — so a replay or a revise that changes only the title never
        re-notifies about the same topic; ``act`` creates the notification (send + ledger in
        one atomic write, so there is no act-crash gap).

        ``window`` (P8) buckets the run's creation instant into the fingerprint so a
        recurring notify sends once per period instead of once-ever: ``""`` ⇒ once-ever
        ``{recipient}:{topic}``; ``daily``/``weekly``/``monthly`` ⇒
        ``{recipient}:{topic}:{window_key}`` (a new period → a fresh key → re-sends). Because
        the journal is workflow_id-scoped (a re-trigger reuses it), the run's ``run_id`` is
        folded into the shell inputs so ``decide`` re-consults the ledger each invocation
        rather than journal-skipping — the ledger, not the journal, is the send authority.
        Returns ``{sent, action, notification_id}``. ``name`` is the capability's site."""
        if self._notify is None:
            raise RuntimeError("send_notification needs a capability (wired by the run driver)")
        notify_fn = self._notify
        sent_check = self._notification_sent
        fingerprint = f"{recipient}:{topic}"
        if window and self.run_started_at is not None:
            from .triggers import window_key

            fingerprint = f"{fingerprint}:{window_key(window, self.run_started_at)}"

        async def decide(_feedback: str | None) -> Verdict:
            already = sent_check is not None and await sent_check(fingerprint)
            return Verdict(kind="duplicate" if already else "new", payload={"key": fingerprint})

        async def act(verdict: Verdict) -> Result:
            if verdict.kind == "duplicate":
                return Result(fields={"sent": False, "action": "skip", "notification_id": ""})
            nid = await notify_fn(recipient, title or topic, body, fingerprint)
            return Result(fields={"sent": True, "action": "send", "notification_id": nid})

        inputs: dict[str, Any] = {
            "recipient": recipient,
            "topic": topic,
            "title": title,
            "body": body,
        }
        if self.run_id:
            # Fold the per-invocation identity so decide re-runs (and re-queries the ledger)
            # each invocation instead of returning the workflow_id-scoped journal's cached
            # ruling — the ledger is the send authority (M1), not the journal.
            inputs["run_id"] = self.run_id
        result = await run_nonidempotent(
            self,
            name=name,
            inputs=inputs,
            decide=decide,
            act=act,
            key=key,
            phase=phase,
            cache=cache,
        )
        return result.fields

    async def update_entity(
        self,
        type_name: str,
        number: int,
        patch: dict[str, Any],
        *,
        phase: str = "commit",
        cache: bool = True,
        retries: int = 3,
    ) -> str:
        """Update a file-first entity (#419) through the framework's ``EntityStore.update``
        — the SAME path the UI and the agent use, never a raw ``wf.write`` (§C, "single
        write path"). Optimistic + conflict-retrying (#429 P2): it re-reads the record's
        version, applies the merge-``patch`` with that version, and on ``EntityConflict``
        (a *parallel run* moved the record) re-reads and retries up to ``retries`` times —
        so two workflow runs updating the same entity never lost-update (this is how gap 5
        "parallel runs hit the same entity" is closed, without a new lock). Journaled +
        skipped on re-run keyed by ``(type, number, patch)`` — the patch is absolute field
        values, so a re-run is a no-op skip, never a double-apply. Returns the new version."""
        from ..entity.catalog import discover_catalog
        from ..entity.store import EntityConflict, EntityStore

        catalog, _diags = await discover_catalog(self._store, self._workspace_id)
        if type_name not in catalog:
            raise StepFailed(f"unknown entity type: {type_name!r}")
        store = EntityStore(
            self._store, self._workspace_id, catalog, on_write=self._entity_write_sink
        )

        async def execute(_feedback: str | None) -> dict[str, str]:
            for _ in range(retries + 1):
                current = await store.get(type_name, number)
                try:
                    updated = await store.update(
                        type_name,
                        number,
                        patch,
                        expected_version=current.version,
                        actor=self.user,
                        origin=self.entity_origin,
                    )
                except EntityConflict:
                    logger.warning(
                        "update_entity %s #%s: version conflict, retrying", type_name, number
                    )
                    continue  # a parallel run moved it — re-read + re-apply on the fresh copy
                return {"version": updated.version}
            logger.warning(
                "update_entity %s #%s: retry budget exhausted after %s retries",
                type_name,
                number,
                retries,
            )
            raise StepFailed(
                f"update_entity {type_name} #{number}: too many version conflicts "
                f"(retried {retries} times) — a parallel run keeps moving it"
            )

        result = await run_step(
            self,
            name="update_entity",
            key=f"{type_name}_{number}_{input_hash(patch)}",
            phase=phase,
            args={"type": type_name, "number": number, "patch": patch},
            execute=execute,
            cache=cache,
        )
        return result["version"]

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
        reference_doc_ids: list[str] | None = None,
        name: str = "",
        key: str = "",
    ) -> str:
        """Deterministic node (manual §8, #111): create-or-update a ``ContextCard`` on an
        existing KB collection as the captured user — the ``→collections`` workflow's
        commit of a filled glossary entry. An existing card for the key is overwritten
        (‘有就更新、沒才新增’), so re-classifying the same term doesn't duplicate it.
        Journaled + skipped on re-run (§9); the ``step_card`` receipt key is the card's
        identity, so a re-run with the same content is a no-op. Returns the card id.

        #518: ``reference_doc_ids`` links the documents that back the card (``None`` ⇒
        say nothing, keeping whatever the card already carries). ``name`` publishes the
        id as ``{steps.<name>.card_id}`` and, as with ingest, moves the receipt to the
        named/scope-keyed location; anonymous calls keep the original content-hashed
        ``step_card`` receipt so existing workflows re-run identically."""
        if self._upsert_card is None:
            raise RuntimeError("upsert_context_card needs a capability (wired by the run driver)")
        upsert_card = self._upsert_card

        async def execute(_feedback: str | None) -> dict[str, Any]:
            card_id = await upsert_card(collection, keys, title, body, reference_doc_ids)
            return {"fields": {"card_id": card_id}} if name else {"card_id": card_id}

        result = await run_step(
            self,
            name=name or "card",
            key=key if name else _card_step_key(keys, title, body),
            phase=phase,
            args={
                "collection": collection,
                "keys": list(keys),
                "title": title,
                # #518: part of the cache key — re-pointing a card's evidence must re-fire
                # the step rather than be masked as already-done.
                "reference_doc_ids": reference_doc_ids,
            },
            execute=execute,
            cache=cache,
        )
        return result["fields"]["card_id"] if name else result["card_id"]

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
                    logger.warning("map: element %s failed (skip+collect): %s", item, exc)
                    failures.append({"item": str(item), "error": str(exc)})

        await asyncio.gather(*(_one(item) for item in items))
        return failures
