"""``Preset.suggestions`` shape + str/dict normalization (#91).

Three behaviours, one per cycle:

* ``Preset.suggestions`` is typed as ``list[Suggestion]``.
* A bare string in the loader-facing dict promotes to
  ``Suggestion(label=s, prompt=s)`` so existing operator YAML keeps
  working (display == send semantics that ``list[str]`` used to have).
* A dict ``{"label": "X", "prompt": "Y"}`` builds a structured
  suggestion directly so operators can split chip text from chip prompt.
"""

from __future__ import annotations

from workspace_app.config.schema import Preset, Suggestion, _preset_from_dict


def test_preset_constructed_directly_with_suggestion_objects():
    """The dataclass field is ``list[Suggestion]`` and accepts the structured
    form without any loader assistance."""
    p = Preset(
        model="ollama_chat/qwen3:14b",
        suggestions=[
            Suggestion(label="SPC", prompt="Show me the SPC analysis."),
        ],
    )
    assert isinstance(p.suggestions[0], Suggestion)
    assert p.suggestions[0].label == "SPC"
    assert p.suggestions[0].prompt == "Show me the SPC analysis."


def test_loader_promotes_bare_string_to_suggestion_with_label_equal_to_prompt():
    """Existing YAML (``suggestions: ["short"]``) keeps working: the loader
    promotes each string ``s`` to ``Suggestion(label=s, prompt=s)`` so the
    visible behaviour matches the old ``list[str]`` semantics.
    """
    p = _preset_from_dict({"model": "m", "suggestions": ["Draft the report"]})
    assert len(p.suggestions) == 1
    s = p.suggestions[0]
    assert isinstance(s, Suggestion)
    assert s.label == "Draft the report"
    assert s.prompt == "Draft the report"


def test_loader_accepts_dict_form():
    """The new structured form lets the operator split chip text from chip
    prompt explicitly: ``{"label": "SPC", "prompt": "Show me ..."}``."""
    p = _preset_from_dict(
        {
            "model": "m",
            "suggestions": [
                {"label": "SPC", "prompt": "Show me the SPC analysis."},
            ],
        }
    )
    assert len(p.suggestions) == 1
    s = p.suggestions[0]
    assert isinstance(s, Suggestion)
    assert s.label == "SPC"
    assert s.prompt == "Show me the SPC analysis."
