"""#624 P2: what we send is governed by a real ceiling, and cutting is spoken.

Two behaviour changes ride together here: history is measured with the
CJK-aware estimator, and the budget is derived from a resolved context limit
instead of the two hardcoded constants. Landing either alone is worse than
landing neither — an accurate estimator against a fabricated 24,000 cap trims
3.6x sooner on Chinese.
"""

from __future__ import annotations

import pytest

from workspace_app.api.turns import history_items
from workspace_app.resources import Message


def _msgs(n: int, chars: int = 400) -> list[Message]:
    return [
        Message(
            role="user" if i % 2 == 0 else "assistant", content="這批晶圓的量測資料" * (chars // 9)
        )
        for i in range(n)
    ]


def test_no_budget_means_nothing_is_dropped():
    """#624's locked default: with no known ceiling we do NOT invent one. A
    long conversation goes out whole and we learn the real limit from the
    response — self-inflicted amnesia is the defect, not the safety net."""
    msgs = _msgs(200)
    dropped: list[int] = []

    items = history_items(msgs, max_messages=0, max_tokens=0, on_trim=dropped.append)

    assert len(items) >= 200  # every message survived (tool msgs expand, so >=)
    assert dropped == []


def test_a_budget_trims_and_reports_how_many_were_dropped():
    """When a real ceiling forces a cut, the count is handed back — today the
    function returns only the survivors, so nobody can be told anything."""
    msgs = _msgs(100)
    dropped: list[int] = []

    history_items(msgs, max_messages=0, max_tokens=2_000, on_trim=dropped.append)

    assert dropped and dropped[0] > 0


def test_the_budget_is_measured_with_the_cjk_estimator():
    """Chinese costs ~1 token/char, not 1/4. A 3,000-char Chinese history must
    NOT fit a 1,000-token budget just because `chars // 4` said 750."""
    msgs = [Message(role="user", content="量測資料異常" * 500)]  # 3,000 CJK chars

    kept = history_items(msgs, max_messages=0, max_tokens=1_000)

    # The newest message is always kept (dropping the current context is worse),
    # so the proof is that it is the ONLY thing that fits.
    assert len(kept) == 1


def test_message_count_cap_is_off_by_default_but_still_honoured_when_set():
    """`max_messages` retires as the governor (memory is bounded by tokens, not
    by "the 41st message"), yet stays available as an explicit operator cap."""
    msgs = _msgs(60, chars=40)

    assert len(history_items(msgs, max_messages=0, max_tokens=0)) >= 60
    assert len(history_items(msgs, max_messages=10, max_tokens=0)) == 10


def _app_with_limit(limit: int | None, *, mute: bool = False):
    """create_app with an operator-declared context ceiling, a scripted runner
    and one rca item — the send path under test.

    ``mute`` scripts a runner that streams no text. #739 routes an over-budget
    thread through compaction first, so a talking runner always produces a
    summary; a mute one is how a spec reaches the fallback where the thread is
    trimmed and the user is told, exactly as before compaction existed."""
    from workspace_app.api import create_app
    from workspace_app.api.events import AgentMetrics, MessageDelta, RunDone
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        # #739: a runner that reports no usage leaves every turn unanchored, so
        # the gauge falls back to a messages-only estimate that cannot see the
        # system prompt or tool schemas — and the compaction trigger then
        # compares that against a whole-request budget and fires far too late.
        # Reporting usage is what a real provider does; without it these specs
        # exercise the fallback rather than the path they are about.
        runner=ScriptedAgentRunner(
            [RunDone()]
            if mute
            else [
                MessageDelta(text="ok"),
                AgentMetrics(
                    phase="final",
                    prompt_tokens=13_000,
                    completion_tokens=2,
                    elapsed_ms=1,
                    exact=True,
                ),
                RunDone(),
            ]
        ),
        get_user_id=lambda: "alice",
        context_limit=limit,
    )
    return TestClient(app), spec, iid


def _thread(spec, iid):
    from specstar import QB

    from workspace_app.resources import Conversation

    rm = spec.get_resource_manager(Conversation)
    rows = list(rm.list_resources((QB["item_id"] == iid).build()))
    return rows[0].data.messages if rows else []


def test_a_full_thread_is_compacted_rather_than_amputated():
    """#739 changed the answer to a full window. It used to be: drop the oldest
    messages — the user's opening request among them — and tell them to start a
    new chat. Now the span is summarised and the conversation continues.

    So the notice must NOT appear on the ordinary path: "開一個新對話" is advice
    the product no longer means, and printing it anyway would teach users to
    throw away threads that no longer need throwing away."""
    # A ceiling that is small but WORKABLE: big enough that, after the reply
    # reserve and the prompt overhead, there is still room for some history —
    # otherwise compaction correctly declines (its own spec) because no amount
    # of summarising can fit a thread into a window the system prompt already
    # fills.
    client, spec, iid = _app_with_limit(16_000)
    for i in range(12):
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    thread = _thread(spec, iid)
    assert [m for m in thread if m.role == "summary"], "the span must be summarised"
    assert not [m for m in thread if m.role == "notice"], (
        "the start-a-new-chat notice is the old policy's voice"
    )


def test_a_summariser_that_returns_nothing_falls_back_to_the_old_cut():
    """Compaction is not allowed to make things worse. A model that answers with
    nothing must leave the thread alone — replacing a span with an empty summary
    loses everything the truncation would have kept — so the turn falls back to
    dropping the oldest messages, and says so, exactly as it did before."""
    client, spec, iid = _app_with_limit(1_000, mute=True)
    for i in range(6):
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    thread = _thread(spec, iid)
    assert not [m for m in thread if m.role == "summary"], "an empty summary is not written"
    notices = [m for m in thread if m.role == "notice"]
    assert notices, "a trimmed turn must still leave a visible notice"
    assert "不會被讀到" in notices[0].content
    assert "新對話" in notices[0].content


def test_the_notice_is_not_repeated_every_turn():
    """Announce at the transition, not on every turn — a notice that fires each
    round becomes wallpaper and stops being read."""
    client, spec, iid = _app_with_limit(1_000, mute=True)
    for i in range(6):
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    assert len([m for m in _thread(spec, iid) if m.role == "notice"]) == 1


def test_an_unknown_ceiling_never_trims_and_never_notices():
    """#624's default: no known ceiling ⇒ send it all. The model registry has
    no entry for the scripted test config, so this is the unknown path."""
    client, spec, iid = _app_with_limit(None)
    for i in range(6):
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    assert [m for m in _thread(spec, iid) if m.role == "notice"] == []


# ── adversarial-review follow-ups ────────────────────────────────────


def _bare_builder():
    """A TurnContextBuilder without its service bundle — `_budget_for` needs only
    the ceiling knobs. One helper so a new field is added in one place rather
    than in every test that reaches past `__init__`."""
    from workspace_app.api.turn_context import TurnContextBuilder

    builder = TurnContextBuilder.__new__(TurnContextBuilder)
    builder._context_limit = None
    builder.learned_limit_fn = None
    builder._catalog_cache = {}
    builder._catalog_fn = lambda model: None  # the registry knows nothing by default
    builder.endpoint_limits_fn = None  # nothing wired ⇒ the rung is skipped
    builder._max_tokens_window_ratio = 0.8
    return builder


def test_sizing_measures_the_same_tool_set_the_runner_sends():
    """M5: `allowed_tools or None` is the alias `_agent_for` warns about in ten
    lines of comment — `[]` means "no tools", not "use the defaults". Sizing
    that charges 13 phantom tools to a config which registers none is measuring
    a different request than the one we send."""
    from workspace_app.api.turn_context import TurnContextBuilder
    from workspace_app.resources import AgentConfig

    empty = AgentConfig(name="t", model="m", system_prompt="", allowed_tools=[])
    builder = TurnContextBuilder.__new__(TurnContextBuilder)

    assert builder._tools_tokens(empty, app_slug=None, profile=None) == 0


def test_an_unknown_ceiling_really_takes_the_unknown_branch():
    """T17: the previous version of this test claimed to exercise the unknown
    path but the model WAS in the registry (budget 28,356) — it passed only
    because the messages were short. Assert the branch itself."""
    from workspace_app.resources import AgentConfig

    unknown_model = AgentConfig(
        name="t", model="openai/some-self-hosted-model-no-registry-knows", system_prompt="s"
    )

    assert _bare_builder()._budget_for(unknown_model) is None


def test_kb_chat_is_not_left_without_any_ceiling():
    """C2 (adversarial review): dropping the two constants to 0 removed KB
    chat's only cap while giving it none of the new machinery — and KB chat is
    the surface that stuffs retrieved passages and whole wiki pages into
    history. It must derive a ceiling like the app chat does, not run uncapped."""
    import inspect

    from workspace_app.api import kb_chat_routes

    src = inspect.getsource(kb_chat_routes.register_kb_chat_routes)
    assert "context_limit" in src, "KB chat must receive the endpoint ceiling too"
    assert "_kb_history_budget" in src or "history_budget" in src, (
        "KB chat must derive a budget, not rely on a constant that now defaults to 0"
    )


def test_what_the_runner_learned_changes_what_the_next_turn_sends():
    """P3 exists to feed P2, and mutation testing showed nothing proved it: with
    `learned=` pinned to None the whole suite stayed green.

    A ceiling learned from traffic — stated in a rejection, or inferred from a
    provider that truncated silently — is worth nothing if the next turn does
    not spend it. The runner would go on relearning the same number forever
    while the budget stayed at whatever the catalog guessed, or at no ceiling at
    all for the self-hosted models that are exactly the case this rung exists
    for.
    """
    from workspace_app.resources import AgentConfig

    # A self-hosted name no registry knows — the production shape.
    cfg = AgentConfig(name="t", model="openai/some-self-hosted-model", system_prompt="s")
    builder = _bare_builder()

    assert builder._budget_for(cfg) is None, "nothing known yet ⇒ send it all"

    builder.learned_limit_fn = lambda model, base_url: 32_768

    spent = builder._budget_for(cfg)
    assert spent is not None, "a learned ceiling must produce a budget"
    assert 0 < spent < 32_768, "and one that leaves room for the prompt and the reply"


def _kb_client_with_limit(limit: int | None):
    """create_app with a declared ceiling and a scripted runner, plus one KB
    chat — the KB send path under test."""
    from workspace_app.api import create_app
    from workspace_app.api.events import MessageDelta, RunDone
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.resources import make_spec
    from workspace_app.resources.kb import EMBED_DIM
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient

    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        context_limit=limit,
    )
    client = TestClient(app)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    chat = client.post("/kb/chats", json={"title": "t", "collection_ids": [cid]}).json()[
        "resource_id"
    ]
    return client, chat


def _kb_thread(client, chat):
    return client.get(f"/kb/chats/{chat}").json()["messages"]


def test_kb_chat_says_when_it_stops_reading_the_thread():
    """Announcing a reduction is unconditional (§3), and KB chat was doing it in
    total silence.

    It is also the surface most likely to need it: retrieved passages and whole
    wiki pages land in this thread, so it reaches a ceiling sooner than app chat
    does — and its users are the ones asking "why did it forget what I told it".
    """
    client, chat = _kb_client_with_limit(1_000)  # tiny ceiling ⇒ everything reduces
    for i in range(6):
        client.post(f"/kb/chats/{chat}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    notices = [m for m in _kb_thread(client, chat) if m["role"] == "notice"]
    assert notices, "a reduced KB turn must leave a visible notice"
    assert "新對話" in notices[0]["content"]


def test_the_kb_notice_is_not_repeated_every_turn():
    """Same rule as the app chat, from the same place — two copies of it would
    be two rules, and one of them would drift."""
    client, chat = _kb_client_with_limit(1_000)
    for i in range(6):
        client.post(f"/kb/chats/{chat}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    assert len([m for m in _kb_thread(client, chat) if m["role"] == "notice"]) == 1


async def test_a_workflow_turn_also_says_when_it_stops_reading_the_thread(monkeypatch):
    """The third surface. A workflow agent node runs on a REAL conversation the
    user can open, and `_common` already computes the note for it — it was just
    dropped on the floor, so a run that quietly forgot its earlier steps looked
    like a model that had gone vague.

    Announcing is unconditional (§3): "nobody is watching right now" is an
    argument for persisting it, not for skipping it.
    """
    import workspace_app.api.app as app_mod
    from workspace_app.api import create_app
    from workspace_app.api.events import MessageDelta, RunDone
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.api.workflow_exec import WorkflowExecutor
    from workspace_app.apps.playground.model import PlaygroundItem
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec()
    captured: dict[str, WorkflowExecutor] = {}
    real = app_mod.WorkflowExecutor
    monkeypatch.setattr(
        app_mod, "WorkflowExecutor", lambda **kw: captured.setdefault("ex", real(**kw))
    )
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
        context_limit=1_000,  # tiny ceiling ⇒ the thread cannot fit
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    conv_rm = spec.get_resource_manager(Conversation)
    rid = conv_rm.create(
        Conversation(
            item_id=item_id,
            messages=[
                Message(role="user", content="量測資料異常" * 200 + str(i)) for i in range(6)
            ],
        )
    ).resource_id

    await captured["ex"].drive_turn(item_id, rid, "u", "接著做", None)

    after = conv_rm.get(rid).data.messages
    assert [m for m in after if m.role == "notice"], "a reduced workflow turn must say so"


def test_the_notice_never_reaches_the_model():
    """The marker is persisted in the thread, so it must be provably invisible
    to the LLM. Otherwise telling the user "I can no longer read the earlier
    messages" would itself consume the little room that is left — and it would
    grow every turn a surface adds one.

    (The FE half is locked by `agentLog.contextNotice.test.ts`.)
    """
    from workspace_app.api.turns import CONTEXT_NOTICE_ROLE

    msgs = [
        Message(role="user", content="量測資料異常"),
        Message(role=CONTEXT_NOTICE_ROLE, content="較早的訊息不會被讀到"),
        Message(role="assistant", content="收到"),
    ]

    items = history_items(msgs, max_messages=0, max_tokens=0)

    assert [i["role"] for i in items] == ["user", "assistant"]
    assert not any("不會被讀到" in str(i) for i in items)


async def test_the_catalog_rung_does_not_block_the_event_loop():
    """§9.12: `catalog_limit` reads like a table lookup and is not — litellm
    resolves an `ollama/*` name by asking the daemon, with no timeout (measured:
    129,781 ms against an address that does not answer). `_budget_for` calls it
    on EVERY turn, from inside `async def build_chat_turn`, and the bundled
    default model is `ollama/*` — so a dev deploy whose Ollama host stops
    answering freezes the whole pod for over two minutes.
    """
    import time

    from workspace_app.context_probe import EndpointLimits
    from workspace_app.resources import AgentConfig

    cfg = AgentConfig(name="t", model="ollama/qwen3:14b", system_prompt="s")
    builder = _bare_builder()
    builder._catalog_fn = lambda model: (  # a hanging daemon
        time.sleep(0.5),
        EndpointLimits(max_input_tokens=40_960, max_tokens=None),
    )[1]

    started = time.perf_counter()
    got = builder._budget_for(cfg)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2, f"the catalog rung blocked the event loop for {elapsed:.2f}s"
    assert got is None, "not back yet ⇒ unknown, which already means 'send it all'"


def test_the_turn_overhead_is_measured_once(monkeypatch):
    """Building the tool schemas to size them costs ~28 ms, on the event loop,
    and the turn was doing it TWICE: once inside `_budget_for` to derive the
    history budget, once again to stamp `context_overhead_tokens`. It is the
    same number both times.
    """
    from workspace_app.api.turn_context import TurnContextBuilder

    real = TurnContextBuilder._tools_tokens
    calls = 0

    def _counting(self, agent_config, **kw):
        # Pass through, never restate the signature: this double counts CALLS,
        # and a copied parameter list makes every new keyword on the real method
        # a TypeError here (it already did once, when `has_subagents` landed).
        nonlocal calls
        calls += 1
        return real(self, agent_config, **kw)

    monkeypatch.setattr(TurnContextBuilder, "_tools_tokens", _counting)

    client, _spec, iid = _app_with_limit(40_000)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "量測資料異常"})

    assert calls == 1, f"the tool schemas were built {calls} times for one turn"


def test_kb_history_budget_does_not_call_the_catalog_every_turn(monkeypatch):
    """The KB-chat budget path must reach `catalog_limit` through the same cached,
    off-loop `deferred_lookup` the app-chat path uses — not a direct synchronous
    call on every turn.

    `catalog_limit` does network I/O (litellm resolves an `ollama/*` name via the
    daemon, untimed), and this runs on the `send_message` request path. Calling it
    raw per turn re-pays that cost every message and, for an `ollama/*` KB model,
    would freeze the event loop — the exact hazard `deferred_lookup` exists to
    prevent (`catalog_limit`'s own docstring says never call it directly on a
    request path).
    """
    from workspace_app import context_budget
    from workspace_app.api import kb_chat_routes
    from workspace_app.resources import AgentConfig

    kb_chat_routes._KB_CATALOG_CACHE.clear()
    calls: list[str] = []
    monkeypatch.setattr(context_budget, "catalog_limits", lambda m: calls.append(m) or None)

    cfg = AgentConfig(name="t", model="openai/self-hosted", system_prompt="s")
    kb_chat_routes._kb_history_budget(cfg, None)
    kb_chat_routes._kb_history_budget(cfg, None)

    assert len(calls) == 1, "the catalog lookup must be cached, not repeated each turn"


# ── the rung for a self-hosted model behind a proxy ──────────────────────


def _cfg(model: str = "our-alias", base_url: str = "http://proxy/v1"):
    from workspace_app.resources import AgentConfig

    return AgentConfig(name="t", model=model, system_prompt="", llm_base_url=base_url)


def test_a_window_the_proxy_states_is_used_and_named_as_a_declaration():
    """The topology this whole rung exists for: the registry does not know the
    alias, `/tokenize` never survives the proxy, and nothing has been learned —
    but the proxy itself was told what the window is."""
    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=131072, max_tokens=8192
    )

    got = builder._context_window(_cfg())
    assert got.tokens == 131072
    assert got.source == "declared"


def test_only_max_tokens_is_derived_and_says_it_was():
    """The real deployment's shape: `max_input_tokens` is null and `max_tokens`
    carries a large number. Using it is a judgement call, so the answer has to
    admit which kind of number it is."""
    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=None, max_tokens=131072
    )

    got = builder._context_window(_cfg())
    assert got.tokens == 104857  # 131072 * 0.8
    assert got.source == "estimated"


def test_the_operators_ratio_is_the_one_applied():
    """Not a constant in the code — see `history.max_tokens_window_ratio`."""
    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder._max_tokens_window_ratio = 0.5
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=None, max_tokens=100_000
    )

    assert builder._context_window(_cfg()).tokens == 50_000


def test_a_stated_window_is_never_scaled_by_the_ratio():
    """`max_input_tokens` already IS the input window. Scaling it would discount
    twice, on top of the prompt, tool schemas, reply reserve and margin the
    budget subtracts later."""
    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder._max_tokens_window_ratio = 0.5
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=131072, max_tokens=131072
    )

    assert builder._context_window(_cfg()).tokens == 131072


def test_the_registry_still_outranks_a_derived_number():
    """An estimate may never displace a figure someone stated — the catalog is
    at least exact about the model it names."""
    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder._catalog_fn = lambda model: EndpointLimits(max_input_tokens=40_960, max_tokens=None)
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=None, max_tokens=131072
    )

    got = builder._context_window(_cfg())
    assert got.tokens == 40_960
    assert got.source == "catalog"


def test_a_silent_proxy_leaves_the_ceiling_unknown():
    """Most endpoints are not a litellm proxy. This rung must add nothing when
    it learns nothing — `unknown` still means send it all."""
    builder = _bare_builder()

    got = builder._context_window(_cfg())
    assert got.tokens is None
    assert got.source == "unknown"


def test_an_empty_base_url_reaches_the_runner_unchanged():
    """`llm_base_url == ""` MEANS "the deploy's endpoint" — only the runner
    knows which that is. Resolving it to `None` here is what made this rung
    silent on a single-endpoint deployment, which is the only shape it was
    written for: every other consumer of this field does the same fallback, and
    this one skipped it."""
    from workspace_app.context_probe import EndpointLimits
    from workspace_app.resources import AgentConfig

    seen: list[tuple[str, str | None]] = []

    def _record(model: str, base_url: str | None) -> EndpointLimits:
        seen.append((model, base_url))
        return EndpointLimits(max_input_tokens=131072, max_tokens=None)

    builder = _bare_builder()
    builder.endpoint_limits_fn = _record

    got = builder._context_window(AgentConfig(name="t", model="our-alias", system_prompt=""))
    assert seen == [("our-alias", None)], "the config's own url is passed through untouched"
    assert got.tokens == 131072


def test_nothing_wired_skips_the_rung_rather_than_failing():
    """Replay and tests run without a runner. The ladder simply has one fewer
    rung there — never an exception on a turn."""
    builder = _bare_builder()
    assert builder._context_window(_cfg()).source == "unknown"


# ── which rung answered has to be visible OUTSIDE the type system ────────


@pytest.fixture(autouse=True)
def _fresh_announcements():
    """The dedupe set is process state, so a test that does not clear it passes
    or fails depending on what ran before it — and would pass for the wrong
    reason once some earlier test happened to announce the same outcome."""
    from workspace_app.api.turn_context import _CEILING_SAID

    _CEILING_SAID.clear()
    yield
    _CEILING_SAID.clear()


def test_the_resolved_ceiling_is_logged_with_its_source(caplog):
    """The plan's own rule — "a number nobody stated must never read like a
    measurement" — held only in the type system. `ContextLimit.source` had no
    production reader at all, so in operation an estimate and a measurement were
    the same number with no way to tell them apart. A deployment whose ceiling
    is a guess derived from an output cap looked exactly like one whose endpoint
    stated its window.

    It matters most where it is least visible: production is reachable only
    through the log aggregator, so this line IS the check that the ladder is
    working, and it names the model so two presets can be told apart."""
    import logging

    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=None, max_tokens=131072
    )

    with caplog.at_level(logging.INFO):
        got = builder._context_window(_cfg())

    assert got.source == "estimated"
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "estimated" in line, line
    assert "104857" in line, line
    assert _cfg().model in line, line


def test_the_same_answer_is_not_logged_every_turn(caplog):
    """One line per distinct outcome per pod. A per-turn line would bury the
    thing it exists to make findable."""
    import logging

    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=131072, max_tokens=None
    )

    with caplog.at_level(logging.INFO):
        for _ in range(5):
            builder._context_window(_cfg())

    said = [r for r in caplog.records if "context ceiling" in r.getMessage()]
    assert len(said) == 1, said


def test_unknown_says_so_too(caplog):
    """The state this whole feature exists to leave. An operator reading the log
    has to be able to see that NOTHING answered — that is the diagnosis, and it
    is invisible if only successes are logged."""
    import logging

    builder = _bare_builder()

    with caplog.at_level(logging.INFO):
        builder._context_window(_cfg())

    assert "unknown" in "\n".join(r.getMessage() for r in caplog.records)


def test_a_derived_ceiling_too_small_to_be_a_window_does_not_drive_compaction():
    """The half of the sanity floor that lived in only one consumer.

    `history_budget` refuses a derived ceiling that cannot hold the fixed
    overhead — but the COMPACTION trigger computes its own budget straight off
    `window.tokens`, and so was untouched by that refusal. With a proxy
    reporting a well-behaved `max_tokens: 4096`, the derived 3,276-token
    "window" leaves a compaction budget of 948, and every thread above a
    paragraph gets compacted on every turn.

    That is strictly worse than the symptom the floor was written for.
    Replaying one message is recoverable; compaction is lossy and irreversible,
    and here it would be driven by a size measured against a ceiling nobody
    stated. The rule has to live where the ceiling is resolved, not in whichever
    consumer happened to be reviewed."""
    from workspace_app.context_probe import EndpointLimits
    from workspace_app.resources import Message

    builder = _bare_builder()
    builder._max_tokens_window_ratio = 0.8
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=None,
        max_tokens=4_096,  # an OUTPUT cap, reported correctly
    )
    cfg = _cfg()
    cfg.system_prompt = "x" * 44_000  # ~11k tokens, the measured low end
    builder._locator = _LocatorFor(cfg)

    messages = [
        Message(role="user" if i % 2 == 0 else "assistant", content="x" * 400) for i in range(20)
    ]
    plan = builder.compaction_plan_for("item", messages)

    assert plan.span == [], "compaction must not be driven by a ceiling nobody stated"


class _LocatorFor:
    def __init__(self, cfg):
        self._cfg = cfg

    def resolve_agent_config(self, item_id):
        return self._cfg


def test_a_stated_ceiling_that_small_still_drives_compaction():
    """The control. When the ceiling was STATED, a thread that overflows it
    genuinely needs compacting, and refusing to would leave the user with a
    conversation that cannot be sent at all."""
    from workspace_app.resources import Message

    builder = _bare_builder()
    builder._context_limit = 4_096  # the operator said so
    cfg = _cfg()
    cfg.system_prompt = "s"
    builder._locator = _LocatorFor(cfg)

    messages = [
        Message(role="user" if i % 2 == 0 else "assistant", content="x" * 4_000) for i in range(20)
    ]
    plan = builder.compaction_plan_for("item", messages)

    assert plan.span, "a stated ceiling is not second-guessed"


def test_the_gauge_shows_no_ceiling_when_nothing_credible_answered():
    """The third consumer of the same number, and the one the user looks at.

    `usage_of` drew its denominator straight off the resolved ceiling, so a
    derived 3,276 that neither the budget nor the compaction trigger is willing
    to act on would still be rendered as the bar's limit — a gauge reading past
    100% beside a chat that never compacts, which is a worse answer than an
    honest "no denominator". The FE is already built for `limit: null`: it shows
    the usage with no bar rather than inventing one."""
    from workspace_app.context_probe import EndpointLimits

    builder = _bare_builder()
    builder._max_tokens_window_ratio = 0.8
    builder.endpoint_limits_fn = lambda model, base_url: EndpointLimits(
        max_input_tokens=None, max_tokens=4_096
    )
    cfg = _cfg()
    cfg.system_prompt = "x" * 44_000
    builder._locator = _LocatorFor(cfg)

    usage = builder.usage_of("item", _msgs(4))

    assert usage.limit is None, "an incredible ceiling must not be drawn as one"
    assert usage.limit_source == "unknown"


def test_the_gauge_still_shows_a_ceiling_anyone_actually_stated():
    """The control — this must not turn the gauge off for everyone."""
    builder = _bare_builder()
    builder._context_limit = 40_960
    cfg = _cfg()
    cfg.system_prompt = "s"
    builder._locator = _LocatorFor(cfg)

    usage = builder.usage_of("item", _msgs(4))

    assert usage.limit == 40_960
    assert usage.limit_source == "config"
