"""Driving a scenario, with a scripted model so no LLM is involved.

The prompt the model receives is assembled by the app's OWN composer, so these
tests also guard against the eval quietly testing a prompt production never
sends.
"""

import inspect

from workspace_app.apps import skills as skills_mod
from workspace_app.skill_eval.runner import (
    APPLIED_HEADER,
    ToolCall,
    Turn,
    applied_block,
    run_scenario,
)
from workspace_app.skill_eval.scenario import Scenario
from workspace_app.skill_eval.tools import Event

SKILL_MD = "---\nname: demo\ndescription: d\n---\n\nBODY OF THE GUIDANCE\n"


class scripted:
    """A model that replies with the given turns in order, then stops talking.
    Records every message list it was handed, so a test can assert on what the
    model was actually told."""

    def __init__(self, *turns: Turn):
        self.seq = list(turns)
        self.seen: list[list[dict]] = []

    def __call__(self, messages: list[dict], tools: list[dict]) -> Turn:
        self.seen.append(messages)
        return self.seq.pop(0) if self.seq else Turn(content="final answer")


def call(name, **args):
    return Turn(tool_calls=[ToolCall(id=f"c{name}", name=name, args=args)])


def test_the_apps_real_resolve_advertises_the_skill_so_read_skill_can_trigger():
    """The system prompt is taken from `AppCatalog.resolve` — the same call a live
    turn makes — rather than reassembled here. Hand-composing it once left out the
    `## Available skills` index, which silently made `read_skill` triggering
    unmeasurable: the model was never told the skill existed."""
    from workspace_app.config.loader import load
    from workspace_app.factories import get_app_catalog

    cfg = get_app_catalog(load()).resolve(app_slug="rca", profile="default")
    assert "RCA Agent" in cfg.system_prompt  # the app's identity
    assert "One tool call per response" in cfg.system_prompt  # apps/_base.md
    assert "Judge code by running it" in cfg.system_prompt  # apps/_sandbox.md
    assert "## Available skills" in cfg.system_prompt
    assert "verify-number" in cfg.system_prompt


def test_the_system_prompt_reaches_the_model_verbatim(tmp_path):
    chat = scripted(Turn(content="ok"))
    run_scenario(chat, Scenario(name="s", prompt="Q?"), tmp_path, system_prompt="SYSTEM HERE")
    assert chat.seen[0][0] == {"role": "system", "content": "SYSTEM HERE"}


def test_the_applied_block_strips_frontmatter_and_keeps_the_body():
    block = applied_block("demo", SKILL_MD)
    assert block.startswith(APPLIED_HEADER)
    assert "### demo" in block
    assert "BODY OF THE GUIDANCE" in block
    assert "description: d" not in block


def test_applied_header_matches_production():
    """The Apply chip's wording is reproduced here rather than imported, because
    production renders it inside an async function that needs a WorkspaceFiles.
    If that text changes, this eval would be testing a prompt nobody sends."""

    def flat(text: str) -> str:
        # Production splits the sentence across adjacent string literals, so the
        # quote characters have to go before the words can be compared.
        return " ".join(text.replace('"', "").split())

    source = flat(inspect.getsource(skills_mod.build_applied_skills_block))
    for line in APPLIED_HEADER.split("\n"):
        if line.strip():
            assert flat(line) in source, line


def test_the_skill_body_reaches_the_model_in_the_user_turn(tmp_path):
    chat = scripted(Turn(content="ok"))
    run_scenario(
        chat,
        Scenario(name="s", prompt="Q?"),
        tmp_path,
        system_prompt="S",
        skill_name="demo",
        skill_md=SKILL_MD,
    )
    assert "BODY OF THE GUIDANCE" in chat.seen[0][1]["content"]
    assert chat.seen[0][1]["content"].endswith("Q?")


def test_the_control_arm_asks_the_same_question_with_no_guidance(tmp_path):
    chat = scripted(Turn(content="ok"))
    run_scenario(chat, Scenario(name="s", prompt="Q?"), tmp_path, system_prompt="S")
    assert chat.seen[0][1]["content"] == "Q?"


def test_an_answer_with_no_tool_calls_ends_the_run(tmp_path):
    t = run_scenario(
        scripted(Turn(content="42")), Scenario(name="s", prompt="q"), tmp_path, system_prompt="S"
    )
    assert (t.answer, t.calls, t.steps, t.ended) == ("42", [], 1, "answered")


def test_tool_calls_are_executed_and_recorded_in_order(tmp_path):
    chat = scripted(
        call("write_file", path="a.py", content="print(1)"),
        call("exec", cmd=["python", "a.py"]),
        Turn(content="done"),
    )
    t = run_scenario(chat, Scenario(name="s", prompt="q"), tmp_path, system_prompt="S")
    assert t.calls == ["write_file", "exec"]
    assert t.ended == "answered"
    assert (tmp_path / "a.py").exists()


def test_only_the_first_tool_call_of_a_reply_runs_and_the_rest_are_nudged(tmp_path):
    """apps/_base.md:9 — multi-tool turns trip a streaming bug for small models,
    so the app takes one. An eval that ran all of them would let guidance pass
    here that stalls in production."""
    chat = scripted(
        Turn(
            tool_calls=[
                ToolCall(id="1", name="write_file", args={"path": "a", "content": "x"}),
                ToolCall(id="2", name="write_file", args={"path": "b", "content": "y"}),
            ]
        ),
        Turn(content="done"),
    )
    t = run_scenario(chat, Scenario(name="s", prompt="q"), tmp_path, system_prompt="S")
    assert t.calls == ["write_file"]
    assert (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()
    nudge = [m for m in chat.seen[-1] if m.get("tool_call_id") == "2"][0]
    assert "one tool call per response" in nudge["content"].lower()


def test_events_are_carried_out_of_the_run_for_scoring(tmp_path):
    (tmp_path / "c.png").write_bytes(b"x")
    chat = scripted(call("show_file", path="c.png"), Turn(content="see chart"))
    t = run_scenario(chat, Scenario(name="s", prompt="q"), tmp_path, system_prompt="S")
    assert t.events == [Event("show_file", "c.png")]


def test_a_tool_raising_is_handed_back_to_the_model_not_the_operator(tmp_path):
    """Swallowing it would score the harness; crashing would lose the run."""
    chat = scripted(call("write_file", content="no path key"), Turn(content="recovered"))
    t = run_scenario(chat, Scenario(name="s", prompt="q"), tmp_path, system_prompt="S")
    assert t.answer == "recovered"
    tool_msg = [m for m in chat.seen[-1] if m.get("role") == "tool"][0]
    assert tool_msg["content"].startswith("tool raised: KeyError")


def test_a_model_that_never_answers_stops_at_the_step_limit(tmp_path):
    chat = scripted(*[call("list_files") for _ in range(10)])
    t = run_scenario(chat, Scenario(name="s", prompt="q"), tmp_path, system_prompt="S", max_steps=3)
    assert (t.steps, t.ended, t.answer) == (3, "step-limit", "")
    assert t.calls == ["list_files"] * 3
