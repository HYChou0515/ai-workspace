"""docs/plan-model-settings.md P3 — the settings everything runs with: the deploy
defaults with the page's patch and keys over them, and the checks Apply runs.

The document the page edits has three parts: `config` (the config.yaml tree),
`apps` (each App's app.json, by slug) and `profiles` (each profile's
_profile.json, by slug then name).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.model_settings.effective import (
    SET,
    effective_app,
    effective_profile,
    effective_settings,
    problems,
)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 9090\n", encoding="utf-8")
    return path


def test_the_config_part_and_its_keys_reach_the_settings(config):
    mini = {"openai-mini": {"model": "openai/gpt-4.1-mini"}}
    document = {"config": {"agents": {"presets": mini}}}
    keys = {"config.agents.presets.openai-mini.llm.api_key": "sk-1"}

    settings = effective_settings(document, config_path=config, env={}, keys=keys)

    preset = settings.agents.presets["openai-mini"]
    assert (preset.model, preset.llm.api_key) == ("openai/gpt-4.1-mini", "sk-1")


def test_an_apps_patch_changes_its_manifest():
    document = {
        "apps": {"playground": {"agent": {"picker": [{"preset": "openai-mini", "name": "Mini"}]}}}
    }

    manifest = effective_app("playground", document)

    assert [(p.preset, p.name) for p in manifest.agent.picker] == [("openai-mini", "Mini")]
    assert manifest.slug == "playground"  # everything not patched is the shipped app.json


def test_a_profiles_patch_changes_it():
    document = {"profiles": {"playground": {"default": {"default_preset": "qwen3-local"}}}}

    assert effective_profile("playground", "default", document).default_preset == "qwen3-local"


def test_a_sound_document_has_no_problems(config):
    assert problems({}, config_path=config, env={}) == []


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # a picker offering a preset that does not exist — today found only
        # when a turn resolves it
        (
            {"apps": {"playground": {"agent": {"picker": [{"preset": "nope", "name": "N"}]}}}},
            "nope",
        ),
        # a profile defaulting to a preset that does not exist
        ({"profiles": {"playground": {"default": {"default_preset": "gone"}}}}, "gone"),
        # an app.json value of the wrong type
        ({"apps": {"playground": {"agent": {"picker": "not-a-list"}}}}, "picker"),
        # what a boot refuses
        ({"config": {"agents": {"presets": {"openai-mini": {"fallbacks": ["x9"]}}}}}, "x9"),
    ],
)
def test_what_cannot_run_is_named(config, document, named):
    found = problems(document, config_path=config, env={})

    assert any(named in problem for problem in found), found


@pytest.mark.parametrize(
    "document",
    [
        # a value nothing re-reads after boot: it would be stored, shown as in
        # force, and do nothing until a restart — refused, pointing at config.yaml
        {"config": {"server": {"port": 1}}},
        {"config": {"kb": {"retrieval": {"context_chars": 9}}}},
        # a whole live parent replaced is the same as touching every key under it
        {"config": {"kb": "x"}},
        # null hides a preset (M12) and means nothing else: anywhere else it would
        # delete a key the loader needs — a value goes back to its default by
        # leaving the patch
        {"config": {"kb": {"wiki": {"llm": None}}}},
        # the infer_modules run knobs ride on a live list but are read at boot
        {
            "config": {
                "agents": {"infer_modules": [{"preset": "infer-modules-default", "parallelism": 2}]}
            }
        },
    ],
)
def test_a_value_read_only_at_boot_is_refused(config, document):
    found = problems(document, config_path=config, env={})

    assert any("config.yaml" in problem for problem in found), found


def test_every_model_choice_can_be_changed_here(config):
    document = {
        "config": {
            "llm": {"base_url": "http://gw:4000"},
            "agents": {
                "presets": {"openai-mini": {"model": "openai/gpt-4.1-mini"}},
                "subagent_models": [],
                "kb_chat": [{"preset": "kb-default", "name": "KB · Mini"}],
            },
            "kb": {
                "retrieval_llm": {"preset": "openai-mini"},
                "card_drafter": {"preset": "openai-mini"},
            },
        }
    }

    assert problems(document, config_path=config, env={}) == []


def test_turning_a_role_on_is_refused(config):
    # Whether a role is on decided which features were wired at boot (M17): the
    # page changes which model a role uses, never whether it exists. (Turning
    # one off needs `null`, refused already — M19.)
    found = problems(
        {"config": {"health": {"judge_llm": {"preset": "openai-mini"}}}},
        config_path=config,
        env={},
    )

    assert any("health.judge_llm" in p and "config.yaml" in p for p in found), found


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # resolved once at boot into the quota ceilings (quota/limits.py) and held
        ({"apps": {"playground": {"resources": {"cpu": "8"}}}}, "apps.playground.resources"),
        # the key that boot dict, and the directory, are found under
        ({"apps": {"playground": {"slug": "other"}}}, "apps.playground.slug"),
        # what boot refuses (validate_workflow_profiles): a workflow with no run.py
        (
            {
                "profiles": {
                    "playground": {
                        "multi": {"workflows": [{"id": "no-such-workflow", "title": "X"}]}
                    }
                }
            },
            "no-such-workflow",
        ),
    ],
)
def test_what_an_app_reads_only_at_boot_is_refused(config, document, named):
    found = problems(document, config_path=config, env={})

    assert any(named in problem for problem in found), found


SECRET_TEXT = {"config": {"agents": {"presets": {"kb-default": {"llm": {"api_key": "sk-typed"}}}}}}


def test_a_secret_typed_into_the_text_is_refused(config):
    # It would sit in the patch — in the diff, in history (M6).
    found = problems(SECRET_TEXT, config_path=config, env={})

    assert any("api_key" in p and "key" in p for p in found), found


def test_the_same_secret_set_as_a_key_is_fine(config):
    keys = {"config.agents.presets.kb-default.llm.api_key": "sk-key"}

    assert problems({}, config_path=config, env={}, keys=keys) == []


def test_a_key_under_a_hidden_preset_is_left_out(config):
    # review r1: hiding a preset that has a key set raised AttributeError in
    # with_keys — a 500 on check / save / Apply until the key was cleared.
    # Keys now land after the merge, only where their setting exists.
    keys = {"config.agents.presets.openai-mini.llm.api_key": "sk-1"}
    hidden = {"config": {"agents": {"presets": {"openai-mini": None}}}}

    assert (
        "openai-mini"
        not in effective_settings(hidden, config_path=config, env={}, keys=keys).agents.presets
    )
    # A refusal, for the reason a person can act on: a picker still offers it.
    found = problems(hidden, config_path=config, env={}, keys=keys)
    assert found and all("is not in agents.presets" in p for p in found), found


def test_a_preset_named_with_a_dot_takes_its_key_and_can_be_hidden(config):
    # Model-style names (`qwen2.5`) are allowed by the schema; a path split on
    # every dot wrote the key under a preset `qwen2` and refused the hide.
    add = {"config": {"agents": {"presets": {"qwen2.5": {"model": "ollama_chat/qwen2.5"}}}}}
    keys = {
        "config.agents.presets.qwen2.5.llm.api_key": "sk-q",
        "config.agents.presets.qwen2.5.env.HF_HOME": "/x",
    }

    preset = effective_settings(add, config_path=config, env={}, keys=keys).agents.presets[
        "qwen2.5"
    ]
    assert preset.llm.api_key == "sk-q" and preset.env["HF_HOME"] == "/x"
    hide = {"config": {"agents": {"presets": {"qwen2.5": None}}}}
    assert problems(hide, config_path=config, env={}) == []


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # M18, review r1: a profile is a folder in the App; one that exists only
        # in the patch passed Apply and was never listed or used anywhere
        ({"profiles": {"playground": {"ghost": {"default_preset": "kb-default"}}}}, "ghost"),
        ({"apps": {"no-such-app": {"title": "X"}}}, "no-such-app"),
        # M19, review r1: null under apps / profiles deleted a key the App needs
        (
            {"apps": {"playground": {"agent": {"suggestions": None}}}},
            "apps.playground.agent.suggestions",
        ),
        (
            {"profiles": {"playground": {"default": {"tools": None}}}},
            "profiles.playground.default.tools",
        ),
    ],
)
def test_what_the_apps_part_cannot_mean_is_refused(config, document, named):
    found = problems(document, config_path=config, env={})

    assert any(named in p for p in found), found


@pytest.mark.parametrize(
    "document",
    [
        # r1 probe: `agents` is live as a whole, so the scope walk stopped there
        # and never looked for a null inside it
        {"config": {"agents": {"presets": {"kb-default": {"model": None}}}}},
        {"config": {"agents": {"kb_chat": None}}},
        {"config": {"agents": {"presets": {"qwen2.5": {"model": None}}}}},
    ],
)
def test_a_null_inside_agents_that_hides_no_preset_is_refused(config, document):
    found = problems(document, config_path=config, env={})

    assert any("null only hides a preset" in p for p in found), found


def test_no_problem_ever_quotes_a_key(config, monkeypatch):
    # M6, review r2: a refusal that quotes a value (`got {x!r}`) handed a key
    # back to whoever asked for the check. Every problem is scrubbed of them.
    import workspace_app.model_settings.effective as effective

    def refuse(*_, **__):
        raise ValueError("agents.presets.x: got 'sk-very-secret-1'")

    monkeypatch.setattr(effective, "load_with_provenance", refuse)

    found = problems(
        {}, config_path=config, env={}, keys={"config.llm.api_key": "sk-very-secret-1"}
    )

    assert found and not any("sk-very-secret-1" in p for p in found), found


@pytest.mark.parametrize(
    "document",
    [
        # review r2: a part that is not an object crashed the check (500) — after
        # PUT /draft had already stored it
        {"apps": ["x"]},
        {"apps": "x"},
        {"profiles": "x"},
        {"profiles": {"playground": ["a"]}},
        {"apps": {"playground": "x"}},
        # ...and a null part was taken as empty: stored, shown, doing nothing
        {"apps": None},
        {"profiles": None},
        {"profiles": {"playground": None}},
        {"config": None},
    ],
)
def test_a_part_of_the_wrong_shape_is_named_not_crashed_on(config, document):
    found = problems(document, config_path=config, env={})

    assert found, document


@pytest.mark.parametrize(
    "document",
    [
        # review r3: the typed-secret walk never went into lists, and a usage entry
        # takes an `llm.api_key` — stored, published, shown in history (M6)
        {
            "config": {
                "agents": {"kb_chat": [{"preset": "kb-default", "llm": {"api_key": "sk-typed"}}]}
            }
        },
        # ...and the defaults show a secret as a mask; applying the mask text
        # back would make it the key
        {"config": {"agents": {"presets": {"kb-default": {"env": {"HF_HOME": SET}}}}}},
        {
            "config": {
                "agents": {
                    "kb_chat": [{"preset": "kb-default", "llm": {"base_url": "*** set ***"}}]
                }
            }
        },
    ],
)
def test_a_secret_or_a_mask_typed_anywhere_is_refused(config, document):
    found = problems(document, config_path=config, env={})

    assert any("key" in p for p in found), found


def test_a_short_key_is_not_scrubbed_out_of_every_message(config):
    # review r3: substring scrubbing of a one-letter key shredded every message
    # and told a guesser which letters the key had.
    found = problems(
        {"config": {"server": {"port": 1}}},
        config_path=config,
        env={},
        keys={"config.agents.presets.kb-default.env.REGION": "e"},
    )

    assert any("read only when the server starts" in p for p in found), found


@pytest.mark.parametrize(
    "document",
    [
        {"config": {"kb": {"retrieval_llm": {"preset": "${SECRET_VAR}"}}}},
        {"config": {"agents": {"presets": {"openai-mini": {"model": "x-${SECRET_VAR}"}}}}},
    ],
)
def test_the_text_never_reads_the_servers_environment(config, document):
    # final round (High): `${VAR}` in the page's text was expanded from the
    # server's environment, and the value came back in a problem message or as
    # the model name test-connection reports — any variable, a DSN, a provider
    # key. The text is literal, and `${` is refused with a reason.
    env = {"SECRET_VAR": "sk-REAL-SECRET-VALUE-0001"}

    found = problems(document, config_path=config, env=env)

    assert any("${" in p for p in found), found
    assert not any("sk-REAL-SECRET-VALUE-0001" in p for p in found), found


def test_the_overlay_is_taken_literally(config):
    document = {"config": {"agents": {"presets": {"openai-mini": {"model": "m-${SECRET_VAR}"}}}}}

    settings = effective_settings(document, config_path=config, env={"SECRET_VAR": "sk-REAL"})

    assert settings.agents.presets["openai-mini"].model == "m-${SECRET_VAR}"


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # final review (High): `prompt_file` joined to the App's folder with no
        # check — `../../proc/self/environ` made the server's environment the
        # system prompt of every turn.
        (
            {"apps": {"playground": {"agent": {"prompt_file": "../../../../proc/self/environ"}}}},
            "prompt_file",
        ),
        ({"apps": {"playground": {"agent": {"prompt_file": "/etc/passwd"}}}}, "prompt_file"),
        # a workflow id is a path too (`workflows/<id>/run.py`): a `../` id could
        # load a run.py from anywhere — the API would import and run it
        (
            {
                "profiles": {
                    "playground": {
                        "multi": {"workflows": [{"id": "../../../../tmp/x", "title": "X"}]}
                    }
                }
            },
            "must be a plain name",
        ),
    ],
)
def test_a_path_that_leaves_the_app_is_refused(config, document, named):
    found = problems(document, config_path=config, env={})

    assert any(named in p for p in found), found


@pytest.fixture
def keyed_config(tmp_path: Path) -> Path:
    path = tmp_path / "keyed.yaml"
    path.write_text(
        "agents:\n"
        "  presets:\n"
        "    openai-mini:\n"
        "      llm:\n"
        "        base_url: https://api.provider.example/v1\n"
        "        api_key: sk-deploy-0000-0000-0001\n"
        "    kb-default:\n"
        "      llm:\n"
        "        base_url: https://api.provider.example/v1\n"
        "        api_key: sk-deploy-0000-0000-0001\n",
        encoding="utf-8",
    )
    return path


MOVED = {
    "config": {"agents": {"presets": {"openai-mini": {"llm": {"base_url": "http://mine.example"}}}}}
}


def test_a_deploy_key_is_not_sent_to_an_endpoint_the_deploy_never_pairs_it_with(keyed_config):
    # final review: change only a preset's base_url and the deploy's key (from
    # config.yaml, readable by no one) went to that server — on every turn after
    # Apply, and on a test call without one.
    found = problems(MOVED, config_path=keyed_config, env={})

    assert any("openai-mini" in p and "key" in p for p in found), found


def test_a_key_set_on_the_page_may_go_to_the_new_endpoint(keyed_config):
    keys = {"config.agents.presets.openai-mini.llm.api_key": "sk-mine-0000-0000-0002"}

    assert problems(MOVED, config_path=keyed_config, env={}, keys=keys) == []


def test_a_new_model_on_the_same_endpoint_keeps_the_deploy_key(keyed_config):
    same = {"config": {"agents": {"presets": {"openai-mini": {"model": "openai/gpt-4.1-mini"}}}}}

    assert problems(same, config_path=keyed_config, env={}) == []


def test_a_usage_entry_may_not_send_a_deploy_key_elsewhere(keyed_config):
    # final review 3: M21 checked presets and roles but not the usage lists —
    # a kb_chat entry overriding only `llm.base_url` kept its preset's key.
    entry = {"preset": "kb-default", "name": "KB", "llm": {"base_url": "https://attacker.example"}}
    document = {"config": {"agents": {"kb_chat": [entry]}}}

    found = problems(document, config_path=keyed_config, env={})

    assert any("kb_chat" in p and "key" in p for p in found), found


def test_a_usage_entry_on_the_deploys_endpoint_is_fine(keyed_config):
    entry = {"preset": "kb-default", "name": "KB", "model": "openai/other-model"}

    document = {"config": {"agents": {"kb_chat": [entry]}}}
    assert problems(document, config_path=keyed_config, env={}) == []


def test_text_in_an_app_may_contain_a_dollar_brace(config):
    # final review 3: app.json / _profile.json are never env-expanded, so `${` in
    # them is just text — refusing it gave a reason that was not true there.
    document = {"profiles": {"playground": {"default": {"description": "Plot ${col} on x"}}}}

    assert problems(document, config_path=config, env={}) == []


def test_a_usage_entry_the_running_catalog_cannot_build_is_refused(config):
    # final review 3: a kb_chat entry on a preset with no prompt_file passed
    # Apply; a boot refuses it, and live it failed every use of the catalog.
    document = {"config": {"agents": {"kb_chat": [{"preset": "openai-mini", "name": "X"}]}}}

    found = problems(document, config_path=config, env={})

    assert any("prompt" in p for p in found), found


@pytest.mark.parametrize("base_url", [[1], {"a": 1}, 7])
@pytest.mark.parametrize("where", [("llm",), ("agents", "presets", "openai-mini", "llm")])
def test_an_endpoint_that_is_not_text_is_refused_not_crashed_on(keyed_config, base_url, where):
    # final review 4: a list base_url loaded, then the key check could not hash
    # it (a preset carrying the deploy's key: TypeError, a 500).
    part: dict = {"base_url": base_url}
    for segment in reversed(where):
        part = {segment: part}
    document = {"config": part}

    found = problems(document, config_path=keyed_config, env={})

    assert any("base_url" in p and "text" in p for p in found), found


@pytest.fixture
def prompted_config(tmp_path: Path) -> Path:
    (tmp_path / "p.md").write_text("You answer.", encoding="utf-8")
    (tmp_path / "other.md").write_text("Anything beside config.yaml.", encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        "agents:\n  presets:\n    mine:\n      model: openai/mine\n      prompt_file: p.md\n",
        encoding="utf-8",
    )
    return path


def _prompt_at(where: str, value: str) -> dict:
    if where == "preset":
        return {"config": {"agents": {"presets": {"kb-default": {"prompt_file": value}}}}}
    entry = {"preset": "kb-default", "name": "X", "prompt_file": value}
    return {"config": {"agents": {"kb_chat": [entry]}}}


@pytest.mark.parametrize(
    "value",
    ["/proc/self/environ", "../../../../proc/self/environ", "config.yaml", "other.md"],
)
@pytest.mark.parametrize("where", ["preset", "usage"])
def test_a_prompt_file_the_page_writes_reads_no_other_file(prompted_config, where, value):
    # final review 5: Apps were confined (M21), the config part was not — a
    # preset's or usage entry's prompt_file of /proc/self/environ put the
    # server's environment into the KB system prompt. Even a file beside
    # config.yaml (config.yaml itself) may hold what no one should read back.
    found = problems(_prompt_at(where, value), config_path=prompted_config, env={})

    assert any("prompt_file" in p for p in found), found


@pytest.mark.parametrize("value", ["pkg:workspace_app.kb.prompts/system.md", "p.md"])
@pytest.mark.parametrize("where", ["preset", "usage"])
def test_a_packaged_prompt_or_one_config_yaml_names_is_fine(prompted_config, where, value):
    found = problems(_prompt_at(where, value), config_path=prompted_config, env={})

    assert not any("prompt_file" in p for p in found), found


def test_a_typo_in_the_draft_is_named_at_its_own_path_not_as_config_yaml(config):
    # final review 5: the loader's "config <file>: unknown key …" named the
    # deploy file and its path on disk, and marked the "config" line.
    document = {"config": {"agents": {"presets": {"kb-default": {"modle": "x"}}}}}

    found = problems(document, config_path=config, env={})

    assert found and found[0].startswith("config.agents.presets.kb-default.modle:"), found
    assert str(config) not in found[0]


@pytest.fixture
def hosted_config(tmp_path: Path) -> Path:
    # The common hosted setup: a key, no base_url — the provider's default.
    path = tmp_path / "hosted.yaml"
    path.write_text(
        "agents:\n"
        "  presets:\n"
        "    kb-default:\n"
        "      model: openai/gpt-4o\n"
        "      llm:\n"
        "        api_key: sk-deploy-0000-0000-0002\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "model", ["huggingface/https://attacker.example", "openai/https://attacker.example"]
)
def test_a_model_that_names_another_destination_may_not_carry_the_deploy_key(hosted_config, model):
    # final review 7: litellm takes the address from the model string too
    # (`huggingface/https://…` is its own api_base) — the (base_url, key) pair
    # stayed the same and the deploy's key went to any URL.
    document = {"config": {"agents": {"presets": {"kb-default": {"model": model}}}}}

    found = problems(document, config_path=hosted_config, env={})

    assert any("deploy's key" in p for p in found), found


def test_another_provider_may_not_carry_the_deploy_key(hosted_config):
    document = {"config": {"agents": {"presets": {"kb-default": {"model": "anthropic/claude-x"}}}}}

    found = problems(document, config_path=hosted_config, env={})

    assert any("deploy's key" in p for p in found), found


@pytest.mark.parametrize("model", ["openai/gpt-4.1", "gpt-4.1"])
def test_another_model_of_the_same_provider_keeps_the_deploy_key(hosted_config, model):
    document = {"config": {"agents": {"presets": {"kb-default": {"model": model}}}}}

    found = problems(document, config_path=hosted_config, env={})

    assert not any("deploy's key" in p for p in found), found


def test_a_usage_list_refusal_does_not_show_the_server_directory(tmp_path):
    # final review 7: building the usage list failed with "file not found at
    # <config dir>" — load_problem drops that path two lines up.
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9090\n", encoding="utf-8")
    entry = {"preset": "qwen3-local", "name": "X", "allowed_tools": ["kb_search"]}

    found = problems({"config": {"agents": {"kb_chat": [entry]}}}, config_path=config, env={})

    assert found and not any(str(tmp_path) in p for p in found), found
