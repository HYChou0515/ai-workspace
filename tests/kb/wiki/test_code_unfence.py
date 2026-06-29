"""Issue #281 follow-up P7 (B8): ``_unfence`` strips the wrapping ``` fence small
models tend to put around a whole page (otherwise the page renders as one code
block), now also tolerating the common "opened a fence but never closed it"
failure and not touching content that legitimately contains code fences."""

from __future__ import annotations

from workspace_app.kb.wiki.code_wiki import _unfence


def test_strips_a_whole_output_wrapper_fence():
    assert _unfence("```markdown\n# Title\n\nbody\n```") == "# Title\n\nbody"


def test_strips_a_lone_unclosed_opening_fence():
    # Small models often open a fence and forget to close it; the stray opener
    # must not make the whole page render as a code block.
    assert _unfence("```markdown\n# Title\n\nbody") == "# Title\n\nbody"


def test_leaves_unfenced_prose_untouched():
    assert _unfence("# Title\n\njust prose") == "# Title\n\njust prose"


def test_keeps_internal_code_fences_in_unfenced_content():
    text = "Here is code:\n\n```python\nx = 1\n```\n\nand more prose"
    assert _unfence(text) == text
