"""`docker/Dockerfile`'s stages (plan-chat-video-export P9, the follow-up
fix): Docker builds the LAST stage when no ``--target`` is given, and the
documented API build command gives none — so the API image is whatever
stage comes last. #823 appended the ``chat-video`` stage at the end, and
from then on ``docker build -t rca-app …`` produced the worker image:
Chromium + ffmpeg (+1.69 GB, measured) and a worker CMD, on every API pod,
against the design the same file states ("API pod 用不到所以不放進
rca-app"). The rule pinned here is the one Docker applies, read off the
text: the final stage is the API, and the worker is reached by name.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"


def _stages() -> list[tuple[str, str]]:
    """``(base, name)`` per ``FROM`` line, in file order."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    return re.findall(r"^FROM\s+(\S+)\s+AS\s+(\S+)\s*$", text, re.MULTILINE)


def test_the_default_build_target_is_the_api_not_the_worker():
    stages = _stages()
    assert stages, "no named FROM stages found"
    base, name = stages[-1]
    assert name == "api", f"the last stage is {name!r}: a bare `docker build` makes THAT image"
    assert base == "app", f"the api stage must be the app stage as is, got FROM {base}"


def test_the_worker_image_is_its_own_named_stage_on_top_of_the_app():
    stages = dict((name, base) for base, name in _stages())
    assert stages.get("chat-video") == "app"
    text = DOCKERFILE.read_text(encoding="utf-8")
    # The two build commands the docs give, both present in the header.
    assert "docker build -t rca-app:latest -f docker/Dockerfile ." in text
    assert "docker build --target chat-video -t rca-app-chat-video:latest" in text
