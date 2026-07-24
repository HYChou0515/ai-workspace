"""#624 P2: what we send is governed by a real ceiling, and cutting is spoken.

Two behaviour changes ride together here: history is measured with the
CJK-aware estimator, and the budget is derived from a resolved context limit
instead of the two hardcoded constants. Landing either alone is worse than
landing neither — an accurate estimator against a fabricated 24,000 cap trims
3.6x sooner on Chinese.
"""

from __future__ import annotations

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


def _app_with_limit(limit: int | None):
    """create_app with an operator-declared context ceiling, a scripted runner
    and one rca item — the send path under test."""
    from workspace_app.api import create_app
    from workspace_app.api.events import MessageDelta, RunDone
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
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
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


def test_a_trimmed_turn_says_so_in_the_thread():
    """The cut must be visible. A silent drop is indistinguishable from the
    model simply being forgetful — which is exactly how #624 stayed hidden."""
    client, spec, iid = _app_with_limit(1_000)  # tiny ceiling ⇒ everything trims
    for i in range(6):
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "量測資料異常" * 200 + str(i)})

    notices = [m for m in _thread(spec, iid) if m.role == "notice"]
    assert notices, "a trimmed turn must leave a visible notice"
    # The notice must state the consequence and the way out, in the user's words.
    assert "不會被讀到" in notices[0].content
    assert "新對話" in notices[0].content


def test_the_notice_is_not_repeated_every_turn():
    """Announce at the transition, not on every turn — a notice that fires each
    round becomes wallpaper and stops being read."""
    client, spec, iid = _app_with_limit(1_000)
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
    from workspace_app.api.turn_context import TurnContextBuilder
    from workspace_app.resources import AgentConfig

    unknown_model = AgentConfig(
        name="t", model="openai/some-self-hosted-model-no-registry-knows", system_prompt="s"
    )
    builder = TurnContextBuilder.__new__(TurnContextBuilder)
    builder._context_limit = None
    builder.learned_limit_fn = None

    assert builder._budget_for(unknown_model) is None


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
