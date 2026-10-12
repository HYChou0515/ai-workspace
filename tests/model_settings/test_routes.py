"""docs/plan-model-settings.md P7 — the page's routes, through the app."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps import overlay
from workspace_app.config.loader import load
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.model_settings.consumers import live_agent_config_catalog
from workspace_app.model_settings.live import LiveSettings
from workspace_app.model_settings.store import SettingsStore
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

ROOT, ALICE = "root", "alice"
BASE = "/api/admin/model-settings"
KB_2 = {"config": {"agents": {"presets": {"kb-default": {"model": "openai/kb-2"}}}}}
API_KEY = "config.agents.presets.kb-default.llm.api_key"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Page:
    def __init__(self, tmp_path: Path, yaml: str = "server:\n  port: 9090\n") -> None:
        config = tmp_path / "config.yaml"
        config.write_text(yaml, encoding="utf-8")
        self.clock = Clock()
        self.live = LiveSettings(
            None,
            config_path=config,
            env={},
            boot=load(config_path=config, env={}),
            clock=self.clock,
        )
        self.user = ROOT
        self.spec = make_spec()
        app = create_app(
            spec=self.spec,
            get_user_id=lambda: self.user,
            superusers=frozenset({ROOT}),
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=ScriptedAgentRunner([]),
            kb_embedder=HashEmbedder(dim=EMBED_DIM),
            kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
            agent_config_catalog=live_agent_config_catalog(self.live, tmp_path),
            live=self.live,
        )
        self.app = app
        self.client = TestClient(app)

    def kb_model(self) -> str:
        self.clock.now += 2
        return self.client.get("/api/kb/agent").json()[0]["model"]


@pytest.fixture
def page(tmp_path: Path):
    yield Page(tmp_path)
    overlay.set_source(None)


def test_the_page_is_not_there_for_anyone_else(page):
    page.user = ALICE

    assert page.client.get(BASE).status_code == 404
    assert page.client.put(f"{BASE}/draft", json={"patch": KB_2}).status_code == 404
    assert page.client.post(f"{BASE}/apply").status_code == 404
    assert page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "x"}).status_code == 404


def test_the_page_shows_the_defaults_and_nothing_published(page):
    body = page.client.get(BASE).json()

    assert body["version"] is None and body["draft"] == {} and body["published"] == {}
    assert "kb-default" in body["defaults"]["config"]["agents"]["presets"]
    assert "playground" in body["defaults"]["apps"]


def test_a_saved_draft_changes_nothing_running(page):
    before = page.kb_model()

    assert page.client.put(f"{BASE}/draft", json={"patch": KB_2}).json() == {"problems": []}

    assert page.client.get(BASE).json()["draft"] == KB_2
    assert page.kb_model() == before


def test_apply_puts_the_draft_in_force(page):
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})

    applied = page.client.post(f"{BASE}/apply").json()

    assert applied["by"] == ROOT and applied["version"]
    assert page.client.get(BASE).json()["version"] == applied["version"]
    assert page.kb_model() == "openai/kb-2"


def test_a_draft_that_cannot_run_is_refused_and_nothing_changes(page):
    bad = {"config": {"agents": {"presets": {"kb-default": {"fallbacks": ["x9"]}}}}}
    page.client.put(f"{BASE}/draft", json={"patch": bad})

    response = page.client.post(f"{BASE}/apply")

    assert response.status_code == 422
    assert any("x9" in p for p in response.json()["detail"]["problems"])
    assert page.client.get(BASE).json()["version"] is None


def test_check_names_the_problem_as_you_type(page):
    found = page.client.post(f"{BASE}/check", json={"patch": {"config": {"server": {}}}}).json()

    assert any("config.server" in p for p in found["problems"])


def test_history_and_rollback(page):
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})
    first = page.client.post(f"{BASE}/apply").json()
    later = {"config": {"agents": {"presets": {"kb-default": {"model": "openai/kb-3"}}}}}
    page.client.put(f"{BASE}/draft", json={"patch": later})
    page.client.post(f"{BASE}/apply")
    assert page.kb_model() == "openai/kb-3"

    rolled = page.client.post(f"{BASE}/rollback", json={"version": first["version"]}).json()

    assert rolled["version"] == first["version"]
    assert page.kb_model() == "openai/kb-2"
    # the draft follows, or the next Apply would re-publish what was rolled back
    assert page.client.get(BASE).json()["draft"] == KB_2
    history = page.client.get(f"{BASE}/history").json()
    assert [h["patch"] for h in history] == [KB_2, later, KB_2]


def test_rollback_to_a_version_that_never_was_is_404(page):
    assert page.client.post(f"{BASE}/rollback", json={"version": "nope"}).status_code == 404


def test_a_key_is_written_never_read_and_in_force_after_apply(page):
    response = page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-live-0000-9876"})
    assert response.status_code == 204
    body = page.client.get(BASE).json()
    assert body["keys"] == {API_KEY: "…9876"}
    assert body["pending_keys"] == [API_KEY]  # waiting for Apply (M20)
    assert "sk-live-0000-9876" not in page.client.get(BASE).text
    page.clock.now += 2
    assert page.live.settings().agents.presets["kb-default"].llm.api_key != "sk-live-0000-9876"

    applied = page.client.post(f"{BASE}/apply").json()

    assert applied["keys_changed"] == [API_KEY]
    assert page.client.get(BASE).json()["pending_keys"] == []
    assert "sk-live-0000-9876" not in page.client.get(f"{BASE}/history").text
    page.clock.now += 2
    assert page.live.settings().agents.presets["kb-default"].llm.api_key == "sk-live-0000-9876"


def test_a_key_where_no_key_belongs_is_refused(page):
    response = page.client.put(
        f"{BASE}/keys", json={"path": "config.agents.presets.kb-default.model", "value": "x"}
    )

    assert response.status_code == 422


def test_a_cleared_key_is_gone(page):
    page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-live-0000-9876"})

    assert page.client.delete(f"{BASE}/keys", params={"path": API_KEY}).status_code == 204

    assert page.client.get(BASE).json()["keys"] == {}


def test_test_connection_calls_the_draft_not_what_is_in_force(page, monkeypatch):
    from workspace_app.kb.llm import LitellmLlm

    monkeypatch.setattr(LitellmLlm, "stream", lambda self, prompt: iter([(self._model, False)]))
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})

    result = page.client.post(f"{BASE}/test-connection", json={"preset": "kb-default"}).json()

    assert result == {
        "ok": True,
        "model": "openai/kb-2",
        "ms": result["ms"],
        "reply": "openai/kb-2",
        "error": "",
    }


def test_a_secret_typed_into_the_draft_is_not_stored(page):
    # M6, review r1: the refusal was only at Apply, so a typed key was saved in
    # the draft and handed back by GET to every superuser.
    typed = {"config": {"agents": {"presets": {"kb-default": {"llm": {"api_key": "sk-typed-1"}}}}}}

    response = page.client.put(f"{BASE}/draft", json={"patch": typed})

    assert response.status_code == 422
    assert any("api_key" in p for p in response.json()["detail"]["problems"])
    assert "sk-typed-1" not in page.client.get(BASE).text


def test_rollback_reaches_a_version_older_than_the_history_page(page):
    # review r1: the route looked the version up in `history()` (newest 50).
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})
    first = page.client.post(f"{BASE}/apply").json()
    store = SettingsStore(page.spec)
    for i in range(51):
        store.publish({"config": {"llm": {"base_url": f"http://{i}"}}}, by="root")

    response = page.client.post(f"{BASE}/rollback", json={"version": first["version"]})

    assert response.status_code == 200
    assert page.kb_model() == "openai/kb-2"


def test_a_rollback_leaves_a_staged_key_staged(page):
    # review r2 (all five lenses), through the route the page calls.
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})
    first = page.client.post(f"{BASE}/apply").json()
    later = {"config": {"agents": {"presets": {"kb-default": {"model": "openai/kb-3"}}}}}
    page.client.put(f"{BASE}/draft", json={"patch": later})
    page.client.post(f"{BASE}/apply")
    page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-staged-1"})

    rolled = page.client.post(f"{BASE}/rollback", json={"version": first["version"]}).json()

    assert rolled["keys_changed"] == []
    assert page.client.get(BASE).json()["pending_keys"] == [API_KEY]
    page.clock.now += 2
    assert page.live.settings().agents.presets["kb-default"].llm.api_key != "sk-staged-1"


def test_a_rollback_to_before_a_preset_existed_is_not_blocked_by_its_key(page):
    # review r2: the key of a preset the earlier version lacks made a half
    # preset, and the rollback was refused with a message about `model`.
    page.client.put(f"{BASE}/draft", json={"patch": {}})
    before = page.client.post(f"{BASE}/apply").json()
    zz = {"config": {"agents": {"presets": {"zz": {"model": "openai/zz-1"}}}}}
    page.client.put(f"{BASE}/draft", json={"patch": zz})
    page.client.put(
        f"{BASE}/keys", json={"path": "config.agents.presets.zz.llm.api_key", "value": "sk-zz"}
    )
    page.client.post(f"{BASE}/apply")

    response = page.client.post(f"{BASE}/rollback", json={"version": before["version"]})

    assert response.status_code == 200, response.json()


def test_a_key_for_a_preset_that_is_not_there_is_refused(page):
    # final review: a typo (`qwen3-locla`) was accepted, listed as set, applied —
    # and used by nothing, with no surface saying so.
    response = page.client.put(
        f"{BASE}/keys",
        json={"path": "config.agents.presets.qwen3-locla.llm.api_key", "value": "sk-typo-0000"},
    )

    assert response.status_code == 422
    assert any("qwen3-locla" in p for p in response.json()["detail"]["problems"])
    assert page.client.get(BASE).json()["keys"] == {}


def test_a_key_for_a_preset_the_saved_draft_adds_is_taken(page):
    zz = {"config": {"agents": {"presets": {"zz": {"model": "openai/zz-1"}}}}}
    page.client.put(f"{BASE}/draft", json={"patch": zz})

    response = page.client.put(
        f"{BASE}/keys", json={"path": "config.agents.presets.zz.llm.api_key", "value": "sk-zz"}
    )

    assert response.status_code == 204


def test_apply_refuses_a_draft_other_than_the_one_its_confirmation_listed(page):
    # final review: Apply took no body and published whatever was stored — a
    # draft another superuser saved after the dialog opened went live under the
    # name of someone who never saw it.
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})
    seen = page.client.get(BASE).json()["draft"]
    theirs = {"config": {"agents": {"presets": {"kb-default": {"model": "openai/theirs"}}}}}
    page.client.put(f"{BASE}/draft", json={"patch": theirs})

    response = page.client.post(f"{BASE}/apply", json={"draft": seen})

    assert response.status_code == 409
    assert page.client.get(BASE).json()["version"] is None
    assert page.client.post(f"{BASE}/apply", json={"draft": theirs}).status_code == 200


def test_apply_refuses_key_changes_other_than_the_ones_its_confirmation_listed(page):
    # final review: the draft was guarded, the pending keys were not — a key set
    # after the dialog opened went into force unlisted.
    page.client.put(f"{BASE}/draft", json={"patch": KB_2})
    page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-later-0000-0000"})

    response = page.client.post(f"{BASE}/apply", json={"draft": KB_2, "pending_keys": []})

    assert response.status_code == 409
    ok = page.client.post(f"{BASE}/apply", json={"draft": KB_2, "pending_keys": [API_KEY]})
    assert ok.status_code == 200


def test_the_page_says_which_keys_are_in_force(page):
    # final review 3: clearing a key never applied is immediate; the page needs
    # to know which keys are in force to say so.
    page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-live-0000-0000-1"})
    assert page.client.get(BASE).json()["applied_keys"] == []

    page.client.post(f"{BASE}/apply")

    assert page.client.get(BASE).json()["applied_keys"] == [API_KEY]


def test_test_connection_works_when_config_yaml_names_a_relative_prompt_file(tmp_path, monkeypatch):
    # final review 4: the endpoint check built the deploy's usage catalog without
    # the config file's directory, so a relative `prompt_file` (the documented
    # form) made every Test connection a 500.
    from workspace_app.kb.llm import LitellmLlm

    (tmp_path / "p.md").write_text("You answer.", encoding="utf-8")
    yaml = (
        "agents:\n"
        "  presets:\n"
        "    mine:\n"
        "      model: openai/mine\n"
        "      prompt_file: p.md\n"
        "      allowed_tools: [kb_search, kb_grep, read_page, read_lines]\n"
        "  kb_chat:\n"
        "    - preset: mine\n"
        "      name: Mine\n"
    )
    page = Page(tmp_path, yaml)
    monkeypatch.setattr(LitellmLlm, "stream", lambda self, prompt: iter([("pong", False)]))
    try:
        reply = page.client.post(f"{BASE}/test-connection", json={"preset": "mine"})
    finally:
        overlay.set_source(None)

    assert reply.status_code == 200, reply.text
    assert reply.json()["ok"] is True


@pytest.mark.parametrize(
    "patch",
    [
        {"config": {"kb": {"retrieval_llm": "s"}}},
        {"config": {"goal": {"checker": 7}}},
    ],
)
def test_a_draft_the_loader_asserts_against_is_named_on_every_route(page, patch):
    # final review 4: the loader validates role references with `assert`; the
    # check caught four error types but not that one, so the saved draft made
    # save, check, Apply and Test connection all 500 until it was replaced.
    saved = page.client.put(f"{BASE}/draft", json={"patch": patch})
    assert saved.status_code == 200 and saved.json()["problems"], saved.text

    assert page.client.post(f"{BASE}/check", json={"patch": patch}).json()["problems"]
    assert page.client.post(f"{BASE}/apply").status_code == 422
    tried = page.client.post(f"{BASE}/test-connection", json={"preset": "kb-default"})
    assert tried.status_code == 200 and tried.json()["ok"] is False
    key = page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-live-0000-0000-1"})
    assert key.status_code == 422


def test_apply_refuses_a_key_changed_after_its_confirmation_listed_it(page):
    # final review 5: the guard compared pending key PATHS — A's dialog listed
    # "set X", B cleared X, the paths still matched and the clear went live.
    page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-first-0000-0000"})
    page.client.post(f"{BASE}/apply")
    page.client.put(f"{BASE}/keys", json={"path": API_KEY, "value": "sk-second-000-0000"})
    seen = page.client.get(BASE).json()
    page.client.delete(f"{BASE}/keys", params={"path": API_KEY})

    response = page.client.post(
        f"{BASE}/apply",
        json={
            "draft": seen["draft"],
            "pending_keys": seen["pending_keys"],
            "key_revision": seen["key_revision"],
        },
    )

    assert response.status_code == 409
    # a sentence to read, not a problem in the draft to fix
    assert isinstance(response.json()["detail"], str)
    assert page.client.get(BASE).json()["applied_keys"] == [API_KEY]


def test_a_preset_published_on_the_page_reaches_an_app_turn(page):
    # final review 5: the only test of this went through a helper production
    # never called; this goes through create_app's own App catalog.
    page.client.put(
        f"{BASE}/draft",
        json={
            "patch": {
                "config": {"agents": {"presets": {"brand-new": {"model": "openai/brand-new-1"}}}},
                "apps": {
                    "playground": {"agent": {"picker": [{"preset": "brand-new", "name": "New"}]}}
                },
            }
        },
    )
    assert page.client.post(f"{BASE}/apply").status_code == 200
    page.clock.now += 2

    config = page.app.state.app_catalog.resolve(app_slug="playground", profile="default")

    assert config.model == "openai/brand-new-1"
