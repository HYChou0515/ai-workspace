"""The builder image's one load-bearing property (#674).

A third-party bundle carries its own portable python and native wheels, so it
runs only on the base it was built against. The builder image exists to make
that base the SAME base the agent's tools execute in — which is a property
nobody can see by reading either Dockerfile alone, and which would rot the
first time someone bumps one of them.

So it is asserted here rather than left as a comment.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _bases(dockerfile: Path) -> list[str]:
    text = dockerfile.read_text("utf-8")
    return [m.group(1) for m in re.finditer(r"^FROM\s+(\S+)", text, re.MULTILINE)]


def test_the_builder_builds_on_the_base_the_sandbox_runs_on() -> None:
    # sandbox-host's LAST stage is the runtime — the image the host jails
    # processes inside, and therefore the ABI a bundle must match. (Its first
    # stage only prebuilds first-party tools and is thrown away.)
    runtime_base = _bases(_REPO / "sandbox-host" / "Dockerfile")[-1]
    builder_base = _bases(_REPO / "tool-builder" / "Dockerfile")[-1]

    assert builder_base == runtime_base, (
        "the builder image and the sandbox runtime have drifted apart — a tool "
        "built by this builder would carry wheels the sandbox cannot load, and "
        "the failure would land at run time inside someone else's tool"
    )


def test_the_builder_image_records_an_abi_anchor_for_every_build() -> None:
    text = (_REPO / "tool-builder" / "Dockerfile").read_text("utf-8")

    # Without it `build-tool` refuses to run: a manifest with no `builder`
    # gives the platform nothing to gate on.
    assert "TOOL_BUILDER_ID" in text
    assert "ARG BUILDER_ID" in text


def test_the_builder_image_carries_no_workspace_app_dependencies() -> None:
    # An author's CI should pull a small image. The build path is stdlib-only
    # (uv comes from the base), so installing the app would be pure weight —
    # and would drag litellm and a data-science stack into a stranger's CI.
    text = (_REPO / "tool-builder" / "Dockerfile").read_text("utf-8")

    assert "uv sync" not in text
    assert "pyproject.toml" not in text


def test_both_images_carry_the_same_abi_anchor_knob() -> None:
    # The gate compares the manifest's `builder` against the host's
    # TOOL_BUILDER_ID. If only one image took the value, every third-party
    # artifact would be refused (or, worse, none would be).
    builder = (_REPO / "tool-builder" / "Dockerfile").read_text("utf-8")
    host = (_REPO / "sandbox-host" / "Dockerfile").read_text("utf-8")

    for text in (builder, host):
        assert "ARG BUILDER_ID" in text
        assert "TOOL_BUILDER_ID=${BUILDER_ID}" in text
