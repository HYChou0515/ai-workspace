"""`server.lookup_targets` — where the "請幫我查" card sends a search
(docs/plan-outside-lookup.md D3).

The backend is air-gapped; the user's browser is not. Each target is a button
on the card that opens `url` with the (edited) query in place of `{q}`, in a new
tab. Unset ⇒ Google alone; set ⇒ the list REPLACES the default, so a deploy that
wants Google too lists it.

A target that cannot work is refused at boot, naming which one: discovering it
when a user presses the button is discovering it in the one place nobody can
fix it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from workspace_app.config.loader import load
from workspace_app.config.schema import DEFAULT_LOOKUP_TARGETS


def _load(tmp_path: Path, yaml: str):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml, encoding="utf-8")
    return load(config_path=cfg, env={})


def test_unset_is_google_alone():
    settings = load(env={})

    assert settings.server.lookup_targets == [
        {"name": "Google", "url": "https://www.google.com/search?q={q}"}
    ]
    assert settings.server.lookup_targets == DEFAULT_LOOKUP_TARGETS


def test_a_list_replaces_the_default_rather_than_adding_to_it(tmp_path: Path):
    settings = _load(
        tmp_path,
        "server:\n"
        "  lookup_targets:\n"
        "    - name: Bing\n"
        "      url: https://www.bing.com/search?q={q}\n"
        "    - name: Wiki\n"
        "      url: http://wiki.example/search?text={q}&lang=zh\n",
    )

    assert settings.server.lookup_targets == [
        {"name": "Bing", "url": "https://www.bing.com/search?q={q}"},
        {"name": "Wiki", "url": "http://wiki.example/search?text={q}&lang=zh"},
    ]


def test_the_default_is_not_shared_between_loads():
    """A frozen dataclass still holds a mutable list; two loads must not hand
    out the same one, or one caller's edit would be every caller's default."""
    a = load(env={}).server.lookup_targets
    b = load(env={}).server.lookup_targets

    assert a == b
    assert a is not b
    assert a is not DEFAULT_LOOKUP_TARGETS


@pytest.mark.parametrize(
    ("value", "sentence"),
    [
        ("{}", "server.lookup_targets must be a list"),
        ("[]", "server.lookup_targets must name at least one target"),
        ("[Google]", "server.lookup_targets[0] must be a mapping with `name` and `url`"),
        (
            "[{name: Google}]",
            "server.lookup_targets[0] must be a mapping with `name` and `url`",
        ),
        (
            "[{name: G, url: 'https://g/?q={q}', icon: x}]",
            "server.lookup_targets[0] has unknown key 'icon'",
        ),
        (
            "[{name: '', url: 'https://g/?q={q}'}]",
            "server.lookup_targets[0].name must be a non-empty string",
        ),
        (
            # docs/plan-lookup-target-choice.md T6: any URL, but a URL — a scheme first.
            "[{name: G, url: 'https://g/?q={q}'}, {name: F, url: 'lookup?pn={q}'}]",
            "server.lookup_targets[1].url must be a URL (`scheme:…`)",
        ),
        (
            "[{name: G, url: 'https://g/?q={q}', description: 3}]",
            "server.lookup_targets[0].description must be a string",
        ),
        (
            "[{name: G, url: 'https://g/?q={q}', preview: 'no'}]",
            "server.lookup_targets[0].preview must be true or false",
        ),
        (
            "[{name: G, url: 'https://g/search'}]",
            "server.lookup_targets[0].url must contain {q}",
        ),
        (
            "[{name: G, url: 'https://g/?q={q}'}, {name: G, url: 'https://h/?q={q}'}]",
            "server.lookup_targets[1].name 'G' is already used",
        ),
        (
            # Two buttons that read the same (review round 1).
            "[{name: Google, url: 'https://g/?q={q}'}, {name: 'Google ', url: 'https://h/?q={q}'}]",
            "server.lookup_targets[1].name 'Google ' is already used",
        ),
    ],
    ids=[
        "mapping",
        "empty",
        "bare-string",
        "no-url",
        "unknown-key",
        "blank-name",
        "no-scheme",
        "description-not-text",
        "preview-not-bool",
        "no-placeholder",
        "duplicate-name",
        "duplicate-name-padded",
    ],
)
def test_a_target_that_cannot_work_refuses_to_boot(tmp_path: Path, value: str, sentence: str):
    with pytest.raises(ValueError, match=re.escape(sentence)):
        _load(tmp_path, f"server:\n  lookup_targets: {value}\n")


def test_a_target_may_say_how_it_searches_and_whether_the_card_opens_it(tmp_path: Path):
    """docs/plan-lookup-target-choice.md T3/T5/T6: a `description` the AI and the
    person read, a `preview` switch for the card's own new-tab button, and a
    `url` the helper interprets — any scheme, not only a web page."""
    settings = _load(
        tmp_path,
        "server:\n"
        "  lookup_targets:\n"
        "    - name: Parts\n"
        "      url: partsdb://lookup?pn={q}\n"
        "      description: The parts database; a full part number only.\n"
        "      preview: false\n",
    )

    assert settings.server.lookup_targets == [
        {
            "name": "Parts",
            "url": "partsdb://lookup?pn={q}",
            "description": "The parts database; a full part number only.",
            "preview": False,
        }
    ]
