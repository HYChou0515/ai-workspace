"""The `make_deck` toolchain (#284 follow-up) must live in the sandbox-host
image, because the prod HTTP host jails processes inside its OWN image and
ignores `SandboxSpec.image`. node + LibreOffice + poppler can't ride the
python-only `/.tools` bundle mechanism (unlike sci-plot), so they're baked into
`sandbox-host/Dockerfile`. Smoke test the contract — a docker build is too slow
for CI (same rationale as `test_image.py`)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_DOCKERFILE = REPO_ROOT / "sandbox-host" / "Dockerfile"


def test_sandbox_host_dockerfile_ships_deck_toolchain():
    text = HOST_DOCKERFILE.read_text()
    # node + npm run the model's pptxgenjs; libreoffice + poppler render slides
    # → images for the review pass; noto-cjk so CJK renders as glyphs not tofu.
    for pkg in ("nodejs", "npm", "libreoffice", "poppler-utils", "fonts-noto-cjk"):
        assert pkg in text, f"{pkg} not installed in sandbox-host/Dockerfile"
    assert "pptxgenjs" in text, "pptxgenjs not installed in sandbox-host/Dockerfile"


def test_legacy_deck_dockerfile_is_gone():
    """`docker/Dockerfile.workspace-deck` targeted the deprecated Docker-sandbox
    model and is a no-op on the HTTP host — it must not linger and mislead."""
    assert not (REPO_ROOT / "docker" / "Dockerfile.workspace-deck").exists()
