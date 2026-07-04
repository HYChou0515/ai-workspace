"""Workflow execution adapter (#54).

A workflow run drives the platform through a handful of host-provided callbacks:
agent turns, sandbox commands, KB ingest, context-card upsert/find, a landed-doc
check, plus the orchestrator's per-run upload-dir / wire-handle / release /
notify-failure hooks. Those were a family of closures (``_wf_*``) inside
``create_app`` that existed only to bind ``create_app``'s services into the
``WorkflowHandle``; ``_wf_wire_handle`` already named the seam.

``WorkflowExecutor`` is that seam as a module: it holds the service bundle once and
exposes the orchestrator's four callbacks (``upload_dir`` / ``wire_handle`` /
``release`` / ``notify_failure``) over the per-handle capability methods. Workflow
execution is now testable against a service bundle + a mock handle, not only
through the ``/run`` endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import msgspec
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources import Conversation
from ..sandbox.protocol import OutputSink, Sandbox
from ..workflow.capabilities import convert_upload, ingest_to_collection, upsert_context_card
from ..workflow.handle import WorkflowHandle
from ..workflow.run import RunStatus, WorkflowRun
from .notifications import notification_sent, notify
from .rca_messages import to_rca_message

if TYPE_CHECKING:
    from ..files import WorkspaceFiles
    from ..kb.ingest import Ingestor
    from ..kb.llm import ILlm
    from .locator import ItemLocator
    from .registry import InvestigationRegistry
    from .turn_context import TurnContextBuilder
    from .turns import ChatTurnEngine, TurnMessage

# The generic sub-agent bridge callable shape (purpose, payload, sink, origin_id, ...).
RunSubagent = Callable[..., Awaitable[tuple[str, list]]]


class WorkflowExecutor:
    """Binds ``create_app``'s services into a workflow run's execution callbacks.
    See the module docstring for the seam this replaces."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        files: WorkspaceFiles,
        registry: InvestigationRegistry,
        sandbox: Sandbox,
        ingestor: Ingestor,
        index_coordinator: Any,
        turn_engine: ChatTurnEngine,
        turn_ctx: TurnContextBuilder,
        locator: ItemLocator,
        run_subagent: RunSubagent,
        ask_llm: ILlm | None = None,
    ) -> None:
        self._spec = spec
        self._files = files
        self._registry = registry
        self._sandbox = sandbox
        self._ingestor = ingestor
        self._index_coordinator = index_coordinator
        self._turn_engine = turn_engine
        self._turn_ctx = turn_ctx
        self._locator = locator
        self._run_subagent = run_subagent
        # #435 P6: the ILlm backing the create_entity cross-origin match (M1-AI, §decide-AI).
        # None ⇒ dedup stays journal-only (self-dedup); a wired model enables cross-match.
        self._ask_llm = ask_llm
        self._conv_rm = spec.get_resource_manager(Conversation)

    def upload_dir(self, slug: str, profile: str) -> str:
        """#198: the active profile's staging folder — the orchestrator threads it onto
        the handle (``wf.upload_dir``) and derives the run's ``input.json`` from it, so a
        workflow globs the same folder the chat attach lands in."""
        from ..apps.profiles import load_profile

        return load_profile(slug, profile).upload_dir

    async def drive_turn(
        self,
        item_id: str,
        chat_key: str,
        captured_user: str,
        prompt: str,
        tools: list[str] | None,
    ) -> str:
        """Run one agent node as a turn on the run's WORKFLOW CHAT (§3, §5.1):
        ``chat_key`` is that chat's conversation id, so turns enqueue + persist there
        (keeping the run's stream separate from the item's other chats). Builds the
        ctx, narrows the tool ceiling to the step's subset, enqueues + awaits, persists
        the produced messages under the captured user, and returns the assistant text."""
        try:
            rid = chat_key
            got = self._conv_rm.get(rid).data
            assert isinstance(got, Conversation)
            conv = got
        except (ResourceIDNotFoundError, AssertionError):
            rid, conv = self._locator.conversation_for(item_id)  # legacy fallback (default chat)
        cfg = self._locator.resolve_agent_config(item_id)
        if cfg is not None and tools is not None:
            # tools= ⊆ the profile's tool ceiling (manual §5.1) — drop anything the
            # profile doesn't already allow, so a step can't widen the boundary.
            ceiling = cfg.allowed_tools or []
            cfg = msgspec.structs.replace(cfg, allowed_tools=[t for t in tools if t in ceiling])
        ctx = await self._turn_ctx.build_workflow_turn(
            item_id,
            agent_config=cfg,
            run_subagent=self._run_subagent,
            history_messages=conv.messages,
        )
        answer: list[str] = []

        def persist(produced: list[TurnMessage]) -> None:
            if produced:
                conv2 = self._conv_rm.get(rid).data  # re-fetch the workflow chat
                assert isinstance(conv2, Conversation)
                # Background step → attribute the persisted turn to the captured
                # user (§15, the job-pod acting-user pattern).
                with self._conv_rm.using(user=captured_user):
                    for tm in produced:
                        conv2.messages.append(to_rca_message(tm))
                    self._conv_rm.update(rid, conv2)
            answer.extend(tm.content for tm in produced if tm.role == "assistant")

        await self._turn_engine.enqueue(chat_key, prompt, ctx, on_complete=persist)
        return "\n".join(answer)

    async def run_sandbox(
        self,
        item_id: str,
        run: str,
        credential: str,
        on_output: OutputSink | None = None,
    ) -> tuple[int, str]:
        """Run a deterministic node's command in the item's sandbox (§5.2), with the
        run-scoped credential injected into its env so a node script can auth
        capability HTTP calls (manual §15). ``on_output`` streams stdout chunks live
        (#178) so a long command shows movement instead of looking dead."""
        session = await self._registry.session(item_id)
        handle = await self._registry.ensure_handle(session)
        import shlex

        env = f"export WF_TOKEN={shlex.quote(credential)}; " if credential else ""
        result = await self._sandbox.exec(handle, ["sh", "-lc", env + run], on_output=on_output)
        with contextlib.suppress(Exception):
            await self._registry.flush(item_id)
        return result.exit_code, result.stdout.decode("utf-8", errors="replace")

    async def ingest(
        self,
        item_id: str,
        captured_user: str,
        collection: str,
        path: str,
        journal_dir: str = "/.workflow/_default",
    ) -> str:
        """The ingest capability (§8) bound to this run's workspace + captured user.
        ``journal_dir`` is the run's journal folder (#136) so the receipt lands under
        the run's workflow folder, not scattered at the workspace root."""
        return await ingest_to_collection(
            self._spec,
            self._ingestor,
            self._files,  # WorkspaceFiles is FileStore-shaped (read/write by workspace id)
            workspace_id=item_id,
            collection=collection,
            path=path,
            user=captured_user,
            # #234: store-then-enqueue — the upload auto-indexes off the request path via
            # the IndexCoordinator, exactly like the KB upload endpoint.
            enqueue=self._index_coordinator.enqueue,
            journal_dir=journal_dir,
        )

    async def convert(self, item_id: str, src: str, dest: str) -> tuple[str | None, str]:
        """The convert capability (#324) bound to this run's workspace: turn the staged
        upload at ``src`` into text (the same KB parsers, no chunk/embed) and stage it at a
        content-coherent path derived from ``dest``, so topic-hub files only the converted
        artifact — never the raw binary."""
        return await convert_upload(
            self._ingestor,
            self._files,
            workspace_id=item_id,
            src=src,
            dest=dest,
        )

    async def upsert_card(
        self, captured_user: str, collection: str, keys: list[str], title: str, body: str
    ) -> str:
        """The upsert-context-card capability (§8, #111) bound to this run's captured
        user — create-or-update by key, so re-classifying a term updates its card
        instead of duplicating it."""
        return upsert_context_card(
            self._spec, collection=collection, keys=keys, title=title, body=body, user=captured_user
        )

    async def find_card(
        self, collection: str, keys: list[str], title: str
    ) -> dict[str, Any] | None:
        """The read-only find-overwrite-target capability (#205) — the existing card a
        commit-time upsert would overwrite, as a plain dict the workflow lib renders into
        the diff "before" snapshot (it stays decoupled from ``ContextCard``)."""
        from ..resources.kb import ContextCard
        from ..workflow.capabilities import find_overwrite_target

        card, ambiguity = find_overwrite_target(
            self._spec, collection=collection, keys=keys, title=title
        )
        if not isinstance(card, ContextCard):
            return None
        return {
            "keys": list(card.keys),
            "title": card.title,
            "body": card.body,
            "ambiguity": ambiguity,
        }

    async def collection_has(self, collection: str, path: str) -> bool:
        """Backs ``check.collection_has`` (§8): did ``path`` land in ``collection``
        (a name or id)? #234: ingest is async, so ``landed`` means the SourceDoc EXISTS
        — the upload succeeded — not that the background index has flipped it to ``ready``."""
        from ..workflow.capabilities import collection_has_doc

        return collection_has_doc(self._spec, collection=collection, path=path)

    async def send_notification(
        self, captured_user: str, recipient: str, title: str, body: str, dedup_key: str
    ) -> str:
        """The send_notification capability (#435 P5): one in-app Notification carrying the
        send-once fingerprint (``dedup_key``) — the create is both the send and the ledger."""
        return notify(
            self._spec,
            recipient=recipient,
            kind="workflow",
            title=title,
            body=body,
            dedup_key=dedup_key,
            actor=captured_user,
        )

    async def notification_already_sent(self, dedup_key: str) -> bool:
        """Backs send_notification's M1 dedup (#435 P5): an indexed Notification query on
        the send-once fingerprint — the store IS the ledger."""
        return notification_sent(self._spec, dedup_key)

    def wire_handle(
        self, wf: WorkflowHandle, run_id: str, item_id: str, captured_user: str, chat_key: str
    ) -> None:
        """Wire a run's ``WorkflowHandle`` to this executor's capabilities. Agent turns
        drive the run's workflow CHAT (chat_key); sandbox / ingest stay on item_id (the
        workspace shared across the item's chats, §3.1)."""
        wf.drive_turn = lambda prompt, tools: self.drive_turn(
            item_id, chat_key, captured_user, prompt, tools
        )
        wf.run_sandbox = lambda run, on_output=None: self.run_sandbox(
            item_id, run, wf.credential, on_output
        )
        wf._ingest = lambda collection, path: self.ingest(
            item_id, captured_user, collection, path, wf.journal_dir
        )
        wf._convert = lambda src, dest: self.convert(item_id, src, dest)
        wf._collection_has = self.collection_has
        wf._upsert_card = lambda collection, keys, title, body: self.upsert_card(
            captured_user, collection, keys, title, body
        )
        wf._find_card = self.find_card
        wf._notify = lambda recipient, title, body, dedup_key: self.send_notification(
            captured_user, recipient, title, body, dedup_key
        )
        wf._notification_sent = self.notification_already_sent
        # #435 P6: the create_entity cross-origin match asks the run's model. collect()
        # is blocking (streams under the hood), so offload it off the loop; left inert
        # (journal-only self-dedup) when no model is wired.
        if self._ask_llm is not None:
            ask = self._ask_llm
            wf.ask_llm = lambda prompt: asyncio.to_thread(ask.collect, prompt)

    def _any_running(self, item_id: str) -> bool:
        """Is any run on this item still RUNNING? Used to decide whether the shared
        sandbox is still needed (§3.1) — the run being released is no longer RUNNING
        (it is terminal / awaiting_human), so this counts only the OTHERS."""
        from specstar import QB

        rm = self._spec.get_resource_manager(WorkflowRun)
        for r in rm.list_resources((QB["item_id"] == item_id).build()):
            if isinstance(r.data, WorkflowRun) and r.data.status is RunStatus.RUNNING:
                return True
        return False

    async def release(self, item_id: str, terminal: bool, chat_key: str) -> None:
        """Free resources when a run ends/pauses (§16). The sandbox + workspace are
        shared across the item's chats (§3.1), so tear the sandbox down only when no
        OTHER run is still running — a parallel run keeps it alive (§3). On terminal,
        drop the finished run's own chat turn session (never the item's other chats)."""
        if not self._any_running(item_id):
            await self._registry.close_session(item_id)
        if terminal:
            await self._turn_engine.forget(chat_key)

    def notify_failure(self, run: WorkflowRun) -> None:
        """In-app failure notification to the item's owner (manual §17)."""
        from ..apps.resolve import find_work_item

        found = find_work_item(self._spec, run.item_id)
        if found is None:  # pragma: no cover - a run always has a live item
            return
        slug, item = found
        phase = run.current_phase or "?"
        notify(
            self._spec,
            recipient=item.owner,
            kind="status",
            title=f"Workflow run failed at “{phase}”",
            link=f"/a/{slug}/items/{run.item_id}",
            actor=run.captured_user,
        )
