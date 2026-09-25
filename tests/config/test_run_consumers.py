"""`server.run_consumers` — one key, three shapes (`docs/plan-run-consumers-list.md`).

`true` starts every in-process consumer, `false` none, a list only those it
names. All three arrive through the real loader — from YAML, and from the
`${RUN_CONSUMERS}` env marker every deployment doc recommends, whose value is
a STRING the loader used to hand straight to the dataclass: `"false"` was
truthy, and the "pure producer" API pod consumed everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.loader import load, load_with_provenance

MARKER = "server:\n  run_consumers: ${RUN_CONSUMERS}\n"


def _load(tmp_path: Path, yaml: str, env: dict[str, str] | None = None):
    p = tmp_path / "config.yaml"
    p.write_text(yaml, encoding="utf-8")
    return load(config_path=p, env=env or {})


def test_the_env_marker_false_means_false(tmp_path: Path) -> None:
    # Reddens on the loader before this plan: `expand_env` returns the env's
    # string and `_build` is `cls(**sub)`, so this was `'false'` — truthy.
    settings = _load(tmp_path, MARKER, {"RUN_CONSUMERS": "false"})

    assert settings.server.run_consumers is False


@pytest.mark.parametrize(
    ("yaml", "env", "expected"),
    [
        ("server:\n  run_consumers: true\n", {}, True),
        ("server:\n  run_consumers: false\n", {}, False),
        ("server:\n  run_consumers: [index, card-gen]\n", {}, ["index", "card-gen"]),
        (MARKER, {"RUN_CONSUMERS": "TRUE"}, True),
        (MARKER, {"RUN_CONSUMERS": " index, card-gen "}, ["index", "card-gen"]),
        ("server:\n  run_consumers: []\n", {}, []),
        ("", {}, True),  # no server block at all: the default is all-in-one
    ],
)
def test_the_three_shapes_from_yaml_and_from_the_env(tmp_path: Path, yaml, env, expected) -> None:
    assert _load(tmp_path, yaml, env).server.run_consumers == expected


def test_a_name_that_is_not_a_jobtype_refuses_to_boot_and_names_the_valid_ones(
    tmp_path: Path,
) -> None:
    # The allow-list's one cost is a JobType left off it; a typo must not become
    # "that queue never moves" — it fails here, beside the unknown-key refusals.
    with pytest.raises(ValueError) as err:
        _load(tmp_path, "server:\n  run_consumers: [index, chat_video]\n")

    text = str(err.value)
    assert "server.run_consumers" in text and "chat_video" in text
    for valid in ("index", "card-gen", "kb-import", "blob-gc", "chat-video"):
        assert valid in text


@pytest.mark.parametrize(
    ("yaml", "env", "fragment"),
    [
        ("server:\n  run_consumers: [index, 3]\n", {}, "strings"),
        (MARKER, {"RUN_CONSUMERS": ""}, "empty"),
        ("server:\n  run_consumers: 1\n", {}, "expected true, false or a list"),
    ],
)
def test_shapes_that_are_none_of_the_three_are_refused(tmp_path: Path, yaml, env, fragment) -> None:
    with pytest.raises(ValueError, match=fragment):
        _load(tmp_path, yaml, env)


def test_separators_with_no_names_are_refused_like_the_empty_string(tmp_path: Path) -> None:
    # `RUN_CONSUMERS=","` split to `[]` — a pure producer spelled by accident,
    # while `""` was refused. Same intent, same answer.
    with pytest.raises(ValueError, match="no JobType names"):
        _load(tmp_path, MARKER, {"RUN_CONSUMERS": ","})


def test_the_refusal_of_a_non_name_offers_true_and_false_too(tmp_path: Path) -> None:
    # `RUN_CONSUMERS=no` (or `0`, `off`) means "none" to the operator who wrote
    # it; the refusal has to offer the spelling that works, not only JobTypes.
    with pytest.raises(ValueError) as err:
        _load(tmp_path, MARKER, {"RUN_CONSUMERS": "no"})

    text = str(err.value)
    assert "'no'" in text and "true / false" in text


def test_a_list_built_from_the_env_marker_is_labelled_env_in_the_provenance(
    tmp_path: Path,
) -> None:
    # The boot dump labels every leaf. `${RUN_CONSUMERS}` = `index,card-gen` is
    # ONE env scalar the loader turns into a list, so its elements have no
    # operator path of their own — and read `# ← default` beside a `false`
    # from the same marker that read `# ← env`. They take the scalar's source.
    p = tmp_path / "config.yaml"
    p.write_text(MARKER, encoding="utf-8")
    settings, prov = load_with_provenance(config_path=p, env={"RUN_CONSUMERS": "index,card-gen"})

    assert settings.server.run_consumers == ["index", "card-gen"]
    assert prov["server.run_consumers[0]"].kind == "env"
    assert prov["server.run_consumers[1]"].kind == "env"
    assert prov["server.run_consumers[0]"].ref == "${RUN_CONSUMERS}"


def test_an_empty_list_the_operator_wrote_is_labelled_config(tmp_path: Path) -> None:
    # `run_consumers: []` is a leaf with nothing to walk; it recorded no source
    # and the dump called the operator's choice a default.
    p = tmp_path / "config.yaml"
    p.write_text("server:\n  run_consumers: []\n", encoding="utf-8")
    _, prov = load_with_provenance(config_path=p, env={})

    assert prov["server.run_consumers"].kind == "config.yaml"
