"""RCA turn driver (#54) — the interactive workspace/chat send path.

Extracted from ``create_app``'s ``_send_into`` closure: append the user message
to a conversation, build the RCA turn context from ITS history, and enqueue the
turn on the chat engine. Shared by the item-level and chat-scoped message
endpoints (wired into ``register_chat_routes`` as ``send_into``).

The closure became ``ChatSendService.send`` with its create_app-local helpers
turned into constructor-injected deps: the sub-agent bridge (``_run_subagent``),
the item locator (``_resolve_agent_config`` / ``_app_context_files``), the turn
context builder, and the file/user/activity/engine services. The two nested
per-turn closures (``_run_subagent_with_depth`` and ``persist``) stay nested —
they close over this turn's body/enhancements/collection scope and the delicate
citation-bubbling logic.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import magic
from fastapi import HTTPException

from ..agent.context import KbSearchBudget, WikiSearchBudget
from ..config.schema import OffHoursSettings
from ..filestore.protocol import FileNotFound
from ..kb.collections import (
    collection_ids_from_json,
    collection_tiers_from_json,
    excluded_ids_from_json,
    read_hub_collections,
    resolve_named_collection_ids,
    resolve_withheld,
)
from ..resources import Conversation, Message
from ..resources.conversation_goal import GOAL_DRIVER, read_goal, upsert_goal
from ..sandbox.protocol import OutputSink
from ..tokens import CallLane
from ..workcalendar import OffHoursCalendar
from .events import GoalUpdated, UserMessage
from .goal_offhours import build_offhours_calendar, owner_is_active, turn_signature
from .goal_wrapup import headline, marker_text, night_transcript, write_summary
from .kb_chat_routes import resolve_max_searches, to_caller_enhancements
from .notifications import notify
from .rca_messages import bubble_kb_citations, to_rca_message
from .timeutil import now_ms
from .turn_gate import admit_turn
from .turns import CONTEXT_NOTICE_ROLE, already_noticed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request
    from specstar import SpecStar

    from ..files import WorkspaceFiles
    from ..filestore.protocol import FileStore
    from ..kb.llm import ILlm
    from ..kb.retriever import Enhancements
    from ..quota.admission import AdmissionGate
    from ..resources.kb import Citation
    from ..users import UserDirectory
    from .activity import ActivityLog
    from .compaction import IConversationCompactor
    from .locator import ItemLocator
    from .request_env import IRequestEnv
    from .schemas import _MessageBody
    from .subagent_bridge import SubagentBridge
    from .turn_context import TurnContextBuilder
    from .turns import ChatTurnEngine, TurnMessage

from ..agent.context import AgentToolContext
from ..context_budget import SUMMARY_ROLE
from .events import Compacting

logger = logging.getLogger(__name__)

# #615: how many consecutive no-progress turns park an unattended goal. Two,
# because one is a blip worth retrying and three is a night already wasted.
_STALL_LIMIT = 2


async def _load_inline_image_urls(
    files: WorkspaceFiles, investigation_id: str, paths: list[str]
) -> list[str]:
    """Read each attached workspace image and encode it as a `data:` URL, so the
    runner can inline it into a vision main model's user message (source A) — the
    model sees the pixels directly, with no `read_image` round-trip through the
    separate VLM. A path that vanished (deleted between upload and send) or isn't
    actually an image is skipped rather than fatal: the turn still runs with
    whatever images survive. Called only when the resolved agent is a VLM."""
    urls: list[str] = []
    for path in paths:
        try:
            data = await files.read(investigation_id, path)
        except FileNotFound:
            continue
        mime = magic.from_buffer(data, mime=True)
        if not mime.startswith("image/"):
            continue
        urls.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
    return urls


def _ran_out_of_steps(outcome: str) -> bool:
    """Did the turn stop because it hit `runner.max_turns`, rather than fail?

    #721: the two are opposite kinds of ending, and collapsing them into "not
    ok" is what made a goal's budget unspendable. A tool that errored means the
    turn went WRONG and, with someone at their desk, belongs back in their hands
    (#613). Running out of steps means it did not go wrong — it did not FINISH,
    which is the one case where continuing is the whole point.

    The thread still shows the step-limit banner either way: what changes is
    whether the goal takes another round, not what the person is told.
    """
    return outcome == "max_turns"


def _last_user_was_the_driver(conv: Conversation) -> bool:
    """Was the turn that just ended started by the goal driver rather than by a
    person? (#615) The driver's round is stored as `role="user"` under the
    goal's setter, so `driven_by` is the only thing that tells them apart."""
    for message in reversed(conv.messages):
        if message.role == "user":
            return message.driven_by == GOAL_DRIVER
    return False


class ChatSendService:
    """Drive an RCA turn for a workspace/chat message: persist the user message,
    build the turn context from the conversation's history, and enqueue it on the
    chat engine. The injected deps replace the create_app closures it captured."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        locator: ItemLocator,
        turn_ctx: TurnContextBuilder,
        # #739: writes the précis that replaces a span too large to send.
        # None ⇒ compaction off, and the old drop-the-oldest behaviour stands.
        compactor: IConversationCompactor | None = None,
        subagent_bridge: SubagentBridge,
        filestore: FileStore,
        files: WorkspaceFiles,
        users: UserDirectory,
        activity: ActivityLog,
        turn_engine: ChatTurnEngine,
        get_user_id: Callable[[], str],
        infer_modules_collection: str,
        infer_modules_enhancements: Enhancements | None,
        infer_modules_reasoning_effort: str | None,
        kb_max_searches_per_turn: int | None = None,
        kb_max_searches_ceiling: int = 10,
        goal_checker_llm: ILlm | None = None,
        goal_max_rounds: int = 3,
        offhours: OffHoursSettings | None = None,
        flush_item: Callable[[str], Awaitable[None]],
        admission: AdmissionGate | None = None,
        request_env: IRequestEnv | None = None,
        send_await_timeout: float = 25.0,
    ) -> None:
        self._spec = spec
        self._locator = locator
        self._turn_ctx = turn_ctx
        self._compactor = compactor
        self._subagent_bridge = subagent_bridge
        self._filestore = filestore
        self._files = files
        self._users = users
        self._activity = activity
        self._turn_engine = turn_engine
        self._get_user_id = get_user_id
        self._infer_modules_collection = infer_modules_collection
        self._infer_modules_enhancements = infer_modules_enhancements
        self._infer_modules_reasoning_effort = infer_modules_reasoning_effort
        self._kb_max_searches_per_turn = kb_max_searches_per_turn
        self._kb_max_searches_ceiling = kb_max_searches_ceiling
        # #613 P3: the goal auto-continue driver. None checker ⇒ the whole
        # feature is inert (and the /goal routes disclose that on the wire).
        self._goal_checker = goal_checker_llm
        self._goal_max_rounds = goal_max_rounds
        # #615: the off-hours budget + window. The CALENDAR is rebuilt per use
        # (see `_offhours_calendar`), so recording a make-up workday takes
        # effect tonight rather than after the next restart.
        self._offhours = offhours or OffHoursSettings()
        self._offhours_max_rounds = self._offhours.max_rounds
        # #492: flush this item's live sandbox to durable at turn-end (guarantee
        # (2)'s Y=1 turn) — a no-op when the item is cold.
        self._flush_item = flush_item
        # The cpu/memory sibling of the workspace-full gate below. None ⇒ no
        # per-person limits configured, so nothing to check.
        self._admission = admission
        # #714: the deploy's request→env impl, or None when it plugged none in
        # (then a turn's tools see the item's env_vars alone, as before).
        self._request_env = request_env
        # #493 symptom 1 (504): how long the POST awaits its own turn before
        # DETACHING it to the background. Snappy turns finish within this and the
        # POST returns after the reply is persisted (the historical behaviour every
        # test + the instant-reply UX rely on); a long agent turn detaches and the
        # POST returns 202 well before an ingress `proxy-read-timeout` (default 60s)
        # would 504 it — the turn keeps running on the engine's worker and the
        # client watches the live SSE stream, refetching the thread on `done`.
        self._send_await_timeout = send_await_timeout
        self._conv_rm = spec.get_resource_manager(Conversation)
        # Strong references to in-flight sends (see `send`): asyncio keeps only a
        # weak one, so an un-referenced task can be collected mid-flight.
        self._inflight: set[asyncio.Task[None]] = set()
        # #613 P3: strong refs to detached goal follow-up tasks (same GC hazard
        # as _inflight — an unreferenced task can vanish mid-flight).
        self._goal_tasks: set[asyncio.Task[None]] = set()

    async def send(
        self,
        investigation_id: str,
        rid: str,
        conv: Conversation,
        engine_key: str,
        body: _MessageBody,
        author: str | None = None,
        lane: CallLane = "background",
        driven_by: str | None = None,
        request: Request | None = None,
    ) -> None:
        """Append the user message, build the turn ctx and enqueue it — see
        :meth:`_send` — but do it in a task this request only WATCHES.

        Everything from persisting the user message to `enqueue` is I/O that can
        outlast the client's connection: a cold sandbox wake, a slow store, image
        loading, context and skill file reads. If the request died in that window
        the message was already persisted while the turn was never created, so the
        composer stayed locked forever waiting for a reply that nobody was ever
        going to produce — and no amount of client-side recovery can invent a turn
        that does not exist.

        `shield` keeps the work running when this request is cancelled, while a
        live request still sees its exceptions exactly as before. The strong
        reference matters: asyncio holds only a weak one, so an un-referenced task
        can be collected mid-flight, which is the very failure being prevented.

        #538: a person holding as much as they may hold is refused outright,
        BEFORE the message is persisted — see `turn_gate.admit_turn` for why the
        gate sits here rather than on each write, and why it reports every limit
        that bound rather than the first one to fire."""
        await admit_turn(self._files, self._admission, investigation_id)
        # Who this turn is for, settled ONCE here rather than again downstream:
        # the request env is composed for this person, and the message is stamped
        # with them, so the two must not be able to disagree.
        author = author or self._get_user_id()
        # #714: and the same placement for the caller's own request-derived
        # variables — resolved HERE, while the request is still open, because by
        # the time a tool is dispatched this POST has long returned. After the
        # gate above, so a refused turn never pays for a credential exchange it
        # is not going to use.
        request_env = await self._resolve_request_env(
            request, user_id=author, item_id=investigation_id
        )
        task = asyncio.create_task(
            self._send(
                investigation_id,
                rid,
                conv,
                engine_key,
                body,
                author=author,
                lane=lane,
                driven_by=driven_by,
                request_env=request_env,
            )
        )
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        await asyncio.shield(task)

    async def compact(
        self,
        item_id: str,
        rid: str,
        conv: Conversation,
        engine_key: str,
        *,
        force: bool = False,
    ) -> bool:
        """Replace the span that no longer fits with a précis of it (#739).

        Three refusals, all cheaper than the alternative:
        no compactor wired, nothing worth compacting, or a summariser that came
        back empty. In the last case we leave the thread alone and let the
        reducer do what it always did — replacing a span with NOTHING is worse
        than the truncation this exists to avoid."""
        if self._compactor is None:
            return False
        at, span = self._turn_ctx.compaction_plan_for(item_id, conv.messages, force=force)
        if not span:
            return False
        # The turn is about to take a whole extra round trip. Say so, or the
        # chat looks frozen for the one turn a user is least expecting it — and
        # say when it is OVER in a `finally`, because two of the three ways out
        # of here write nothing at all. The manual path publishes no turn
        # afterwards, so an unfinished notice would stay up for every viewer
        # until somebody started something unrelated.
        self._turn_engine.publish(engine_key, Compacting(replaced=len(span)))
        try:
            ctx = AgentToolContext(
                investigation_id=item_id,
                agent_config=self._locator.resolve_agent_config(item_id),
            )
            try:
                text = await self._compactor.summarise(span, ctx=ctx)
            except Exception:  # noqa: BLE001 — a failed summary must not fail the turn
                logger.warning("chat_send: compaction failed for item %s", item_id, exc_info=True)
                return False
            if not text.strip():
                logger.warning("chat_send: compaction produced nothing for item %s", item_id)
                return False
            conv.messages.insert(at, Message(role=SUMMARY_ROLE, content=text, created_at=now_ms()))
            self._conv_rm.update(rid, conv)
            return True
        finally:
            self._turn_engine.publish(engine_key, Compacting(replaced=len(span), done=True))

    async def _resolve_request_env(
        self, request: Request | None, *, user_id: str, item_id: str
    ) -> dict[str, str]:
        """What the request behind this send contributes to the turn's tool env.

        Empty whenever there is no seam or no request. The second case is not an
        edge: the goal driver (#615) re-enters this same method to continue a
        chat with nobody watching, and it holds no request — which is the whole
        of what a turn without a person behind it inherits, since nothing about
        the caller is stored anywhere for it to pick up.

        A failing impl FAILS THE SEND, before the user's message is persisted —
        the same placement as the quota gate above, and for the same reason: a
        turn that runs anyway would run as nobody in particular and return an
        answer that looks right. An impl that would
        rather degrade catches its own errors and returns ``{}``.

        The impl's own message is deliberately NOT relayed to the client: only
        the impl knows whether it built that string out of the very cookie it was
        reading. The server log keeps the traceback.
        """
        if self._request_env is None or request is None:
            return {}
        try:
            return await self._request_env.env_for(request, user_id=user_id, item_id=item_id)
        except Exception:
            logger.exception("chat_send: request env source failed for item %s", item_id)
            raise HTTPException(
                # Not 502/503/504: the chat client reads those as "an idle
                # gateway cut the POST while the turn runs" and keeps waiting for
                # a reply that this refusal guarantees will never come.
                status_code=500,
                detail={"error": "request_env_failed"},
            ) from None

    # ── #613 P3: goal auto-continue ─────────────────────────────────────

    def _maybe_continue_goal(
        self,
        produced: list[TurnMessage],
        investigation_id: str,
        rid: str,
        engine_key: str,
        author: str,
    ) -> None:
        """Turn-persisted hook: judge the chat's goal and maybe drive another
        round. Runs inside the turn task (the worker is awaiting it), so the
        follow-up is spawned DETACHED — awaiting it here would deadlock the
        queue.

        A user's Stop ends everything, at any hour: a human just intervened, and
        running away from them is the one unforgivable behaviour. Other failures
        are passed along as an `outcome` for the follow-up to judge — during
        office hours it still hands straight back (#613), but overnight a single
        blip has to be survivable, because there is nobody to hand back TO."""
        if self._goal_checker is None:
            return
        failure = next((m for m in produced if m.role == "error"), None)
        if failure is not None and failure.error_kind == "cancelled":
            return
        outcome = (failure.error_kind or "error") if failure is not None else "ok"
        task = asyncio.create_task(
            self._goal_followup(investigation_id, rid, engine_key, author, outcome)
        )
        self._goal_tasks.add(task)
        task.add_done_callback(self._goal_tasks.discard)

    async def _goal_followup(
        self, investigation_id: str, rid: str, engine_key: str, author: str, outcome: str = "ok"
    ) -> None:
        """One goal checkpoint: cheap-LLM verdict → met / continue / exhaust.

        The round is bumped BEFORE the continuation is enqueued (a crash must
        not forget spent rounds and loop from zero), and the engine's cancel
        epoch is sampled around the check so a user's Stop while we were
        judging stands — the same baseline pattern as the workflow driver."""
        from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

        from .goal_checker import check_goal_met, transcript_tail
        from .schemas import _MessageBody

        try:
            goal = read_goal(self._spec, rid)
            if goal is None or goal.state != "active":
                return
            conv = self._conv_rm.get(rid).data
            assert isinstance(conv, Conversation)
            # #615: a failed turn is only retried when nobody is there to take
            # over. At a desk, handing straight back is right (#613's rule) —
            # and deciding that BEFORE the checker call keeps a broken turn from
            # also costing a model call.
            unattended = (
                goal.offhours
                and _last_user_was_the_driver(conv)
                and self._offhours_calendar().is_offhours(datetime.now(UTC))
            )
            if outcome != "ok" and not _ran_out_of_steps(outcome) and not unattended:
                return
            baseline = await self._turn_engine.cancel_epoch(engine_key)
            checker = self._goal_checker
            assert checker is not None  # guarded by _maybe_continue_goal
            # An errored turn produced no new evidence, so there is nothing for
            # the checker to judge — skip the call and treat it as unmet. A turn
            # that merely ran out of steps DID produce evidence (#721), and it
            # may even have finished the job on its last one, so it is judged
            # like any other.
            met = (outcome == "ok" or _ran_out_of_steps(outcome)) and await asyncio.to_thread(
                check_goal_met, checker, goal.condition, transcript_tail(conv.messages)
            )
            # Re-read: the user may have cleared or replaced the goal while the
            # checker ran — their edit wins over our stale verdict.
            current = read_goal(self._spec, rid)
            if current is None or current.state != "active" or current.condition != goal.condition:
                return
            if met:
                current.state = "met"
                upsert_goal(self._spec, current, user=current.set_by)
                await self._hand_over(rid, engine_key, current, "met")
                return
            # #615: which budget is this continuation spending? A night's
            # rounds are counted separately, so a long night cannot make the
            # goal read as exhausted against the (much smaller) work-hours cap
            # the next morning.
            # Re-read the thread too, not just the goal: the checker call took
            # seconds, and if its owner spoke during them the decision below has
            # to see that. Judging "is anyone there?" off a stale copy is how an
            # unattended agent talks over the person it works for.
            latest = self._conv_rm.get(rid).data
            assert isinstance(latest, Conversation)
            after_hours = current.offhours and self._offhours_calendar().is_offhours(
                datetime.now(UTC)
            )
            driver_round = _last_user_was_the_driver(latest)
            if current.offhours and driver_round and not after_hours:
                # The morning edge: the turn that was running when the window
                # closed has now finished, and that is where an unattended chain
                # stops. The goal stays ACTIVE — it is not exhausted, it is
                # waiting for tonight, and the sweeper will pick it up.
                await self._hand_over(rid, engine_key, current, "window")
                return
            if driver_round and after_hours and self._owner_is_active(latest):
                # Its owner came back mid-night. Stand down rather than talk
                # over them; the sweeper releases tonight's claim and re-takes
                # it once the chat goes quiet again.
                self._publish_goal(engine_key, current)
                return
            # #615: the self-destruct gate. A failed turn, or one that made the
            # byte-identical tool calls as the last, counts as no progress; two
            # in a row and the goal parks for a person. Counted separately from
            # the round budget, so recovering from a blip costs a round while
            # being stuck costs the night instead of thirty rounds of it.
            #
            # #721: running out of steps is NOT one of those failures. Reading it
            # as no-progress meant a long job — the exact thing a budget of
            # rounds exists to serve — was parked for a human two rounds in.
            # Circling is still caught, by the signature, which is the judgement
            # that actually distinguishes it; "the turn ended badly" was only
            # ever a proxy for it.
            signature = turn_signature(latest)
            no_progress = (outcome != "ok" and not _ran_out_of_steps(outcome)) or (
                bool(signature) and signature == current.last_signature
            )
            current.stall_count = current.stall_count + 1 if no_progress else 0
            current.last_signature = signature
            if current.stall_count >= _STALL_LIMIT:
                current.state = "stalled"
                upsert_goal(self._spec, current, user=current.set_by)
                await self._hand_over(rid, engine_key, current, "stalled")
                return
            spent = current.offhours_rounds_used if after_hours else current.rounds_used
            budget = self._offhours_max_rounds if after_hours else self._goal_max_rounds
            if spent >= budget:
                current.state = "exhausted"
                upsert_goal(self._spec, current, user=current.set_by)
                await self._hand_over(rid, engine_key, current, "exhausted")
                return
            if after_hours:
                current.offhours_rounds_used += 1
                spent = current.offhours_rounds_used
            else:
                current.rounds_used += 1
                spent = current.rounds_used
            upsert_goal(self._spec, current, user=current.set_by)
            self._publish_goal(engine_key, current)
            if await self._turn_engine.cancel_epoch(engine_key) != baseline:
                return  # the user hit Stop while we were judging — stand down
            fresh = self._conv_rm.get(rid).data
            assert isinstance(fresh, Conversation)
            body = _MessageBody(
                content=(
                    f"[goal] 尚未達成,繼續朝目標推進(第 {spent}/{budget} 輪):{current.condition}"
                )
            )
            # Through the full send path (quota gate, visible user message,
            # broadcast) — as the goal's setter, since this task has no request
            # context to resolve a user from.
            await self.send(
                investigation_id,
                rid,
                fresh,
                engine_key,
                body,
                author=current.set_by or author,
                driven_by=GOAL_DRIVER,
            )
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return  # the chat was deleted mid-flight
        except Exception:
            # #721: a step-capped turn now reaches the checker, where before it
            # returned early — so an outage on that endpoint is newly reachable
            # here. Hand back rather than continue: the verdict is unknown, and
            # "unknown" must fall toward the behaviour we know is safe, which is
            # the one a person is already watching. Escaping instead would only
            # surface as "Task exception was never retrieved" — the goal would
            # stop either way, but nothing would say why.
            logger.exception("chat_send: goal follow-up failed for chat %s", rid)

    def _notice_history_reduced(self, rid: str, note: str) -> None:
        """Tell the thread that older messages no longer reach the model (#624).

        The wording and the once-per-thread rule live in `turns` because THREE
        surfaces persist this marker (app chat, KB chat, workflow turns) — a
        rule kept in three places is a rule that will hold in two of them."""
        conv = self._conv_rm.get(rid).data
        assert isinstance(conv, Conversation)
        if already_noticed(conv.messages):
            return  # already told, at the transition
        # Already composed by `history_items`, which is the only place that
        # knows whether the reduction lost anything (#739).
        text = note
        conv.messages.append(Message(role=CONTEXT_NOTICE_ROLE, content=text, created_at=now_ms()))
        self._conv_rm.update(rid, conv)
        logger.info("chat_send: history reduced for chat %s — %s", rid, note)

    def _offhours_calendar(self) -> OffHoursCalendar:
        return build_offhours_calendar(self._spec, self._offhours)

    def _owner_is_active(self, conv: Conversation) -> bool:
        return owner_is_active(conv, datetime.now(UTC), self._offhours)

    async def start_offhours_round(self, conversation_id: str) -> None:
        """#615: begin an off-hours stretch for ``conversation_id``.

        The sweeper only ever calls this once per stretch; from here the ordinary
        turn-end driver (`_goal_followup`) carries the goal, so there is exactly
        one thing continuing it and no second scheduler to keep in step."""
        goal = read_goal(self._spec, conversation_id)
        if goal is None or goal.state != "active" or not goal.offhours:
            return  # changed under us between the sweep and now
        conv = self._conv_rm.get(conversation_id).data
        assert isinstance(conv, Conversation)
        goal.offhours_rounds_used += 1
        # Bump BEFORE enqueuing: a crash must not forget a spent round and let
        # the budget restart from zero every night.
        upsert_goal(self._spec, goal, user=goal.set_by)
        engine_key = self._locator.engine_key(conv.item_id, conversation_id)
        self._publish_goal(engine_key, goal)
        body = _MessageBody(
            content=(
                f"[goal] 下班時間,繼續朝目標推進(第 {goal.offhours_rounds_used}/"
                f"{self._offhours_max_rounds} 輪):{goal.condition}"
            )
        )
        await self.send(
            conv.item_id,
            conversation_id,
            conv,
            engine_key,
            body,
            author=goal.set_by,
            driven_by=GOAL_DRIVER,
        )

    async def _hand_over(self, rid: str, engine_key: str, goal, ending: str) -> None:  # noqa: ANN001
        """#615 P5: close a goal out — the thread's marker, the bell, the broadcast.

        A run that ended while nobody was watching gets a written hand-over and a
        notification, because otherwise its owner arrives to a thread they did
        not read and has to reconstruct the night themselves. A goal that only
        ever ran with someone at the keyboard gets the marker alone: they
        watched it happen, and a bell for it would be noise that teaches them to
        ignore the next one.
        """
        unattended = goal.offhours and goal.offhours_rounds_used > 0
        summary = ""
        if unattended and self._goal_checker is not None:
            conv = self._conv_rm.get(rid).data
            assert isinstance(conv, Conversation)
            summary = await asyncio.to_thread(
                write_summary,
                self._goal_checker,
                condition=goal.condition,
                ending=ending,
                transcript=night_transcript(conv.messages),
            )
        self._append_goal_marker(rid, marker_text(ending, goal.condition, summary))
        if unattended:
            self._ring_the_bell(rid, goal, ending, summary)
        self._publish_goal(engine_key, goal)

    def _ring_the_bell(self, rid: str, goal, ending: str, summary: str) -> None:  # noqa: ANN001
        """Tell the goal's owner their overnight run ended. Best-effort — a
        notification that fails must not undo the ending that already happened."""
        try:
            conv = self._conv_rm.get(rid).data
            assert isinstance(conv, Conversation)
            slug = self._locator.slug_of(conv.item_id)
            link = f"/a/{slug}/{conv.item_id}" if slug else ""
            notify(
                self._spec,
                recipient=goal.set_by,
                kind="agent_done",
                title=headline(ending, goal.condition),
                body=summary,
                link=link,
            )
        except Exception:  # noqa: BLE001 — the ending stands with or without the bell
            logger.exception("goal hand-over notification failed for %s", rid)

    def _append_goal_marker(self, rid: str, text: str) -> None:
        """A `role="goal"` marker in the thread — rendered by the FE, and by
        design never replayed into LLM history (`history_items` only folds
        user/assistant/tool/error; #199's lesson rules out `system`)."""
        conv = self._conv_rm.get(rid).data
        assert isinstance(conv, Conversation)
        conv.messages.append(Message(role="goal", content=text, created_at=now_ms()))
        self._conv_rm.update(rid, conv)

    def _publish_goal(self, engine_key: str, goal) -> None:  # noqa: ANN001 — ConversationGoal (late import cycle)
        self._turn_engine.publish(
            engine_key,
            GoalUpdated(
                goal={
                    "condition": goal.condition,
                    "set_by": goal.set_by,
                    "rounds_used": goal.rounds_used,
                    "state": goal.state,
                    "max_rounds": self._goal_max_rounds,
                }
            ),
        )

    async def _send(
        self,
        investigation_id: str,
        rid: str,
        conv: Conversation,
        engine_key: str,
        body: _MessageBody,
        author: str,
        lane: CallLane = "background",
        driven_by: str | None = None,
        request_env: dict[str, str] | None = None,
    ) -> None:
        """Append the user message to conversation ``rid``, build the RCA turn ctx
        from ITS history, and enqueue the turn on ``engine_key`` (item_id for the
        default chat, the chat_id otherwise — manual §3). Shared by the item-level
        and chat-scoped message endpoints.

        ``author`` arrives already settled (``send`` does it): the same person
        must stamp the message and be the one the request env was composed for,
        and two defaults for one question is how they come to differ."""
        # #43: stamp the sender so a shared workspace's chat shows who said what,
        # and broadcast the message to live viewers (below, before the turn runs).
        created = now_ms()
        conv.messages.append(
            Message(
                role="user",
                content=body.content,
                author=author,
                created_at=created,
                answers=body.answers,
                # #615: mark a driver's round, so "has a human spoken lately?"
                # is answerable without sniffing the message text.
                driven_by=driven_by,
            )
        )
        self._conv_rm.update(rid, conv)
        # #739: compact BEFORE the turn is built — the thread is final for this
        # turn (the user's message is in) and the model has not been called yet.
        #
        # This DOES lengthen the POST. `send` awaits `asyncio.shield(task)`, and
        # shield stops a client disconnect from CANCELLING the work; it does not
        # stop the caller waiting for it. Measured: a summariser that takes 1s
        # adds 1s to `POST /messages`. An earlier version of this comment claimed
        # the opposite, which was simply wrong.
        #
        # It stays here anyway: the alternative is compacting inside the turn,
        # after the model has already been handed a thread that does not fit.
        # The cost is disclosed instead — `Compacting` streams while it runs, so
        # the wait is explained rather than silent.
        await self.compact(investigation_id, rid, conv, engine_key)
        logger.info(
            "chat_send: user %s sent message to item %s (chat %s)",
            author,
            investigation_id,
            rid,
        )

        # Topic Hub §5/§7 + #280: the item's collection set (collections.json),
        # read ONCE — the flat union scopes the turn's deterministic glossary /
        # resolve_collection; the rank-ordered tiers drive ask_knowledge_base's
        # priority fallback. Both empty for Apps without the file.
        hub_data = await read_hub_collections(self._filestore, investigation_id)
        hub_collection_ids = collection_ids_from_json(hub_data)
        hub_collection_tiers = collection_tiers_from_json(hub_data)
        # Global-collection concept: globals the item's collections.json flagged
        # `exclude: true` — removed from the (tier ∪ global) baseline (grill D2 mode 3).
        hub_excluded = excluded_ids_from_json(hub_data)
        # Composer knowledge-search depth: applies to this turn's KB
        # lookups. The bridge wrapper forwards it to the kb_chat
        # sub-agent only — infer_modules' focused classification probe
        # keeps the operator defaults.
        caller_enh = to_caller_enhancements(body.enhancements)
        # #66: resolve infer_modules' configured collection NAME → ids ONCE for
        # this whole turn (not per step). "" ⇒ None ⇒ the bridge searches all
        # collections (backward-compatible). A configured-but-missing name → []
        # ⇒ kb_search finds nothing and the classifier falls back to taxonomy.
        infer_coll_ids = resolve_named_collection_ids(self._spec, self._infer_modules_collection)
        # #334 Q6: ONE kb_search budget for the WHOLE turn, shared by every
        # ask_knowledge_base call below — the composer's per-message pick (clamped
        # to [0, ceiling]) or, absent one, the operator default. infer_modules is
        # NOT scoped by it (it keeps the operator default, a focused classifier).
        kb_budget = KbSearchBudget(
            max_calls=resolve_max_searches(
                body.max_kb_searches,
                default=self._kb_max_searches_per_turn,
                ceiling=self._kb_max_searches_ceiling,
            )
        )
        # #537 follow-up: the wiki twin — one turn-wide allowance shared the same
        # way. Always passed, so the sub-agent's wiki cap is the user's pick (or
        # the operator default) instead of the old unstated-→-unlimited.
        wiki_budget = WikiSearchBudget(
            max_calls=resolve_max_searches(
                body.max_wiki_searches,
                default=self._kb_max_searches_per_turn,
                ceiling=self._kb_max_searches_ceiling,
            )
        )

        async def _run_subagent_with_depth(
            purpose: str,
            payload: str,
            emit: OutputSink | None = None,
            origin_id: str | None = None,
            collection_ids: list[str] | None = None,
            withheld_sink: list[str] | None = None,
        ) -> tuple[str, list[Citation]]:
            # kb_chat uses the COMPOSER's live depth + effort (#65); infer_modules
            # uses its OWN configured depth + effort + a single configured
            # collection (#66, a focused classifier).
            #
            # #280: for kb_chat, the caller (ask_knowledge_base, after resolving
            # its `rank` → a priority tier) passes the tier's `collection_ids`;
            # `None` ⇒ no tier scoping ⇒ search the whole KB (today's behaviour).
            # #334 Q6: only kb_chat (ask_knowledge_base) draws from the turn's
            # shared budget; infer_modules keeps the operator default (bud=None ⇒
            # the bridge seeds a fresh budget from its own max_searches).
            if purpose == "kb_chat":
                enh = caller_enh
                reff = body.reasoning_effort
                colls = collection_ids
                bud = kb_budget
                wiki_bud = wiki_budget
            elif purpose == "infer_modules":
                enh, reff = (
                    self._infer_modules_enhancements,
                    self._infer_modules_reasoning_effort,
                )
                colls = infer_coll_ids
                bud = None
                wiki_bud = None
            else:  # pragma: no cover
                enh, reff, colls, bud, wiki_bud = None, None, None, None, None
            return await self._subagent_bridge.run(
                purpose,
                payload,
                emit,
                origin_id,
                enhancements=enh,
                reasoning_effort=reff,
                collection_ids=colls,
                budget=bud,
                wiki_budget=wiki_bud,
                # Permission-disclosure: forward the parent turn's withheld
                # accumulator so the KB sub-agent's disclosed sources bubble up.
                withheld_sink=withheld_sink,
                # Global-collection concept: the item's collections.json excludes
                # apply to the KB-answer scope (bridge resolves (tier ∪ global) \
                # excluded). infer_modules keeps its focused single collection.
                excluded_collection_ids=hub_excluded if purpose == "kb_chat" else None,
                # #605: the composer's per-chat disclosure toggle rides the body.
                disclosure=body.disclosure if purpose == "kb_chat" else None,
                # The sub-agent runs on the lane of the turn that spawned it.
                lane=lane,
            )

        # ONE bridge for every sub-agent the RCA tools may invoke
        # (ask_knowledge_base, infer_modules, future ones) drives the turn with the
        # investigation's attached agent + the composer's per-turn depth/effort/scope.
        agent_config = self._locator.resolve_agent_config(investigation_id)
        ctx = await self._turn_ctx.build_chat_turn(
            investigation_id,
            agent_config=agent_config,
            run_subagent=_run_subagent_with_depth,
            # Cross-turn memory: prior dialogue (excludes the user msg just added).
            history_messages=conv.messages[:-1],
            reasoning_effort=body.reasoning_effort,
            kb_enhancements=caller_enh,
            collection_ids=hub_collection_ids,
            collection_tiers=hub_collection_tiers,
            acting_user=author,
            speaker=self._users.get(author),
            # Interactive only when the caller says a person is waiting — the goal
            # driver re-enters this same path with nobody watching.
            call_lane=lane,
            # #380: skills applied THIS turn — so read_skill exempts them from the
            # disable gate (their bodies are already preloaded into the prompt).
            apply_skills=body.apply_skills or [],
            # #714: what the POST's own cookies/headers contributed, resolved
            # back when the request was still open. The item's env_vars are
            # merged on top of these.
            request_env=request_env,
            # #613: this thread's Conversation id — the update_todos tool's row key.
            conversation_id=rid,
        )

        # #624: the turn had to leave part of the thread out. Say so — once, at
        # the transition. A silent cut is indistinguishable from the model being
        # forgetful, which is how this stayed invisible for so long; a notice on
        # EVERY subsequent turn would become wallpaper just as fast.
        if ctx.history_reduced_note:
            self._notice_history_reduced(rid, ctx.history_reduced_note)

        # Source A (#…): a vision-capable main model reads attached images
        # directly — inline them into this turn's user message so the model sees
        # the pixels with no `read_image` round-trip through the separate VLM.
        # Text-only models leave this empty and use `read_image` as before; the
        # image also persists as a workspace file, so `read_image` still works.
        if agent_config is not None and agent_config.vision and body.image_paths:
            ctx.turn_image_urls = await _load_inline_image_urls(
                self._files, investigation_id, body.image_paths
            )

        def persist(produced: list[TurnMessage]) -> None:
            # Persist the agent's reply + tool outputs so re-entering the
            # workspace shows them, not just the user's own messages.
            if produced:
                conv2 = self._conv_rm.get(rid).data  # re-fetch THIS chat (not the default)
                assert isinstance(conv2, Conversation)
                # Citations live on `ctx.subagent_citations` — a dict
                # keyed by TOOL NAME (the surface that produced them).
                # Per name, lists are in CALL ORDER, so we keep one
                # cursor per name and pair the Nth bucket entry with
                # the Nth tool message bearing that name. Assistant
                # messages that quote `[N]` bubble against the shared
                # seen-so-far pool (most-recent call wins for marker
                # collisions), so a `[3]` after both an ask_kb call AND
                # an infer_modules call resolves to whichever of them
                # surfaced marker 3 most recently. Tool messages without
                # any stashed citations keep `citations=[]`.
                tool_idx: dict[str, int] = {}
                seen_subagent: list[list[Citation]] = []
                for tm in produced:
                    msg = to_rca_message(tm)
                    name = tm.tool_name
                    pool = ctx.subagent_citations.get(name) if name is not None else None
                    if pool is not None and name is not None:
                        idx = tool_idx.get(name, 0)
                        if idx < len(pool):
                            msg.citations = list(pool[idx])
                            seen_subagent.append(pool[idx])
                        tool_idx[name] = idx + 1
                    elif tm.role == "assistant" and seen_subagent:
                        msg.citations = bubble_kb_citations(tm.content, seen_subagent)
                    # Permission-disclosure: the turn's ask_knowledge_base sub-agents
                    # bubbled read_meta-only sources into ctx.withheld_collection_ids;
                    # chip them on the assistant answer (resolved to id+name+owner).
                    if tm.role == "assistant" and ctx.withheld_collection_ids:
                        msg.withheld = resolve_withheld(self._spec, ctx.withheld_collection_ids)
                    conv2.messages.append(msg)
                self._conv_rm.update(rid, conv2)
            self._activity.record(
                "agent_turn_complete",
                "Agent finished a turn",
                {"investigation_id": investigation_id},
            )
            logger.info("chat_send: turn completed for item %s", investigation_id)
            # #613 P3: the turn is persisted — maybe the chat's goal wants another
            # round. Spawned DETACHED: persist runs inside the turn task (the
            # worker is awaiting it), so awaiting a follow-up turn here would
            # deadlock — the queue only advances once this task ends.
            self._maybe_continue_goal(produced, investigation_id, rid, engine_key, author)

        # Topic Hub §6: prepend the App's context_files (e.g. MEMORY.md +
        # collections.json) as a labelled, authoritative block — re-derived fresh from
        # the live FileStore each turn and handed ONLY to the agent. The persisted user
        # message + the broadcast UserMessage stay clean (block never enters history),
        # so it is idempotent + replay-safe. "" for Apps that declare no context_files.
        from ..apps.context_files import build_context_block
        from ..apps.skills import build_applied_skills_block, build_workspace_skills_block

        block = await build_context_block(
            self._filestore, investigation_id, self._locator.context_files(investigation_id)
        )
        # #298: advertise the skills the user co-created in THIS workspace, read
        # live each turn (through the same file facade the agent writes with, so a
        # skill saved last turn shows up now). Injected like context_files —
        # never persisted into history.
        skills_block = await build_workspace_skills_block(
            self._files, investigation_id, self._locator.skill_prefs_of(investigation_id)
        )
        # #380: skills the user picked to APPLY this turn — hard-preload each body
        # so the model applies it without a read_skill round-trip. One-shot: built
        # from the per-message `apply_skills`, injected like the blocks above, never
        # persisted. Overrides a disabled toggle (resolve_skill_body ignores prefs).
        applied_block = (
            await build_applied_skills_block(
                self._files,
                investigation_id,
                self._locator.slug_of(investigation_id),
                self._locator.profile_of(investigation_id),
                body.apply_skills,
            )
            if body.apply_skills
            else ""
        )
        prefix = "\n\n".join(p for p in (block, skills_block, applied_block) if p)
        turn_content = f"{prefix}\n\n{body.content}" if prefix else body.content

        # #43: broadcast the human's message to every live viewer, then queue the
        # turn and await ITS completion. The queue serializes concurrent users on
        # the shared sandbox/files (a new message no longer cancels a running
        # turn — Stop does). Live turn events reach all viewers via GET .../stream
        # (item-level / default chat) or the chat-scoped stream (other chats).
        self._turn_engine.publish(
            engine_key,
            UserMessage(author=author, content=body.content, created_at=created),
        )
        # #492: flush the item's live sandbox to durable when THIS turn ends, so
        # durable lags by at most one turn (guarantee (2)). Runs on the engine's
        # worker, off this POST's back; a flush failure never fails the turn.
        logger.debug(
            "chat_send: enqueue turn for item %s on engine %s (await<=%.0fs)",
            investigation_id,
            engine_key,
            self._send_await_timeout,
        )
        fut = self._turn_engine.enqueue(
            engine_key,
            turn_content,
            ctx,
            on_complete=persist,
            on_turn_end=lambda: self._flush_item(investigation_id),
        )
        # #493 symptom 1 (504): await THIS turn's completion, but only up to a
        # deadline — then DETACH it so a long turn can't hang the POST until the
        # ingress `proxy-read-timeout` fires a 504. `shield` keeps the turn's
        # completion future alive across our timeout (the worker still resolves it
        # via `fut.set_result`), so a detach is not a cancel. Fast turns resolve
        # `fut` well within the deadline → the POST returns after the reply is
        # persisted, exactly as before; slow turns run on in the background and the
        # client follows the live SSE stream.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(fut), timeout=self._send_await_timeout)
