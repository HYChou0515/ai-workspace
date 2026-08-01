"""The third-party tool artifact contract (#674) — pure, no I/O.

A tool author's CI emits two files next to each other:

    tool.manifest.json   the metadata the platform gates on
    tool.tar.zst         the bundle it describes (.venv/ + python/ + launch
                         + commands.json + schemas/ — the same shape
                         ``prebuild`` already produces for first-party tools)

This module is the half that BOTH sides read: the builder writes a manifest
with it, the platform parses and gates on one. It therefore stays free of the
agents SDK and of any I/O — the builder image must be able to import it
without dragging in the app, and fetching/extracting belong host-side.

Three anchors carry the design:

``builder``
    The ABI anchor. A bundle carries its own portable python and native
    wheels, so it is only runnable on the base it was built against. A
    mismatch is refused at resolve time rather than left to segfault mid-run.
``bundle.sha256``
    The integrity anchor, and the content-address key the host caches under.
    Note what it is NOT: the manifest travels with the bundle, so whoever can
    publish one can publish both. It proves the bytes arrived intact, not that
    they are trustworthy — trust is the artifact URL's push rights.
``name``
    Checked against the name the platform registered, never taken from the
    manifest as authority: a local name is ours to arbitrate (`app.json`'s
    key), so two authors may both call their tool ``data-fetch``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

#: The only manifest layout this platform understands. A newer artifact is
#: refused rather than parsed on a guess.
FORMAT_VERSION = 1


class ArtifactError(Exception):
    """Base for every way an artifact can be refused."""


class ManifestError(ArtifactError):
    """The manifest is unreadable, incomplete, or a format we don't know."""


class IncompatibleArtifact(ArtifactError):
    """Well-formed, but not for this deployment — wrong build base, wrong
    architecture, or a different tool than the one we registered."""


class ChecksumMismatch(ArtifactError):
    """The fetched bytes are not the bytes the manifest describes."""


@dataclass(frozen=True)
class CommandSpec:
    """One command the bundle exposes, as the author's CLI declared it.

    Mirrors ``registry.CommandInfo`` field for field on purpose — the
    platform maps one to the other — but is defined here so this module
    stays importable without the agents SDK."""

    name: str
    description: str
    params_json_schema: dict[str, Any]


@dataclass(frozen=True)
class BundleRef:
    """Where the bytes' identity lives: the content-address key + its size."""

    sha256: str
    size: int


@dataclass(frozen=True)
class SourceRef:
    """Provenance only. The platform never clones this, and never treats
    "it came from git" as a reason to trust the bundle."""

    git: str
    sha: str


@dataclass(frozen=True)
class Manifest:
    """A parsed, structurally-valid ``tool.manifest.json``.

    Structurally valid is not the same as acceptable: compatibility with THIS
    deployment is a separate gate (`check_compatible`)."""

    format_version: int
    name: str
    version: str
    commands: tuple[CommandSpec, ...]
    builder: str
    python: str
    arch: str
    bundle: BundleRef
    source: SourceRef | None
    #: The signed certificate raising this tool's size limit, verbatim, or
    #: ``None`` for the default limit. It rides with the artifact so the
    #: platform checks the same certificate the author built against instead
    #: of an operator having to go and ask for it. Optional and last, so a
    #: manifest written before certificates existed still parses.
    grant: str | None = None


def parse_manifest(raw: bytes) -> Manifest:
    """Parse manifest bytes, or raise ``ManifestError`` naming the problem.

    Every failure arrives as ``ManifestError``: the caller is a resolve step
    whose job is to tell an operator WHICH artifact is wrong and how, so a
    bare ``KeyError`` leaking out of here would be unactionable."""
    try:
        body = json.loads(raw)
    except ValueError as exc:  # JSONDecodeError
        raise ManifestError(f"manifest is not valid json: {exc}") from exc
    if not isinstance(body, dict):
        raise ManifestError(f"manifest must be a json object, got {type(body).__name__}")
    got = body.get("format_version")
    if got != FORMAT_VERSION:
        raise ManifestError(
            f"manifest format_version {got!r}, this platform understands "
            f"{FORMAT_VERSION} — the builder image and the platform are out of step"
        )
    try:
        bundle = body["bundle"]
        src = body.get("source")
        return Manifest(
            format_version=FORMAT_VERSION,
            name=body["name"],
            version=body["version"],
            commands=tuple(
                CommandSpec(
                    name=c["name"],
                    description=c["description"],
                    params_json_schema=c["params_json_schema"],
                )
                for c in body["commands"]
            ),
            builder=body["builder"],
            python=body["python"],
            arch=body["arch"],
            bundle=BundleRef(sha256=bundle["sha256"], size=bundle["size"]),
            source=SourceRef(git=src["git"], sha=src["sha"]) if src else None,
            grant=body.get("grant"),
        )
    except (KeyError, TypeError) as exc:
        raise ManifestError(f"manifest is missing or malformed at {exc}") from exc


def check_compatible(manifest: Manifest, *, builder: str, arch: str) -> None:
    """Refuse an artifact this deployment cannot RUN — wrong build base, wrong
    architecture. Raises ``IncompatibleArtifact``; returns None when the bytes
    could at least execute here.

    Says nothing about whether they are ALLOWED to. Identity and admission
    come from the certificate the platform signed (`grant.admit`), not from
    the name an author happened to give their command — which is what let two
    authors' `data-fetch` collide, and what a manifest can claim for itself
    anyway."""
    if manifest.builder != builder:
        raise IncompatibleArtifact(
            f"{manifest.name!r} was built against {manifest.builder!r}, "
            f"this host runs {builder!r} — rebuild it with the current builder image"
        )
    if manifest.arch != arch:
        raise IncompatibleArtifact(
            f"{manifest.name!r} was built for {manifest.arch!r}, this host is {arch!r}"
        )


def verify_bundle(data: bytes, ref: BundleRef) -> None:
    """Confirm the fetched bytes are the ones the manifest describes.

    Raises ``ChecksumMismatch``; returns None when they match. This is an
    INTEGRITY check, not a trust one — see the module docstring."""
    if len(data) != ref.size:
        # Checked first, and separately, because the common failure is not
        # tampering: an expired artifact URL answers with a small error page.
        # "hash mismatch" would be true and useless; the sizes say what happened.
        raise ChecksumMismatch(
            f"bundle is {len(data)} bytes, manifest declares {ref.size} — "
            "truncated download, or the URL answered with an error page "
            "(an expired GitLab artifact does exactly this)"
        )
    got = hashlib.sha256(data).hexdigest()
    if got != ref.sha256:
        raise ChecksumMismatch(f"bundle hashes to {got}, manifest declares {ref.sha256}")


def render_manifest(manifest: Manifest) -> bytes:
    """Serialise a manifest for the builder to publish.

    The counterpart to `parse_manifest`; keeping both here is what makes the
    round-trip testable, so a builder-image change cannot start emitting
    something this platform would refuse."""
    body: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "name": manifest.name,
        "version": manifest.version,
        "commands": [
            {
                "name": c.name,
                "description": c.description,
                "params_json_schema": c.params_json_schema,
            }
            for c in manifest.commands
        ],
        "builder": manifest.builder,
        "python": manifest.python,
        "arch": manifest.arch,
        "bundle": {"sha256": manifest.bundle.sha256, "size": manifest.bundle.size},
    }
    if manifest.source is not None:
        body["source"] = {"git": manifest.source.git, "sha": manifest.source.sha}
    if manifest.grant is not None:
        body["grant"] = manifest.grant
    # Indented and newline-terminated: a manifest lands in CI logs and in
    # review diffs, where a single long line helps nobody.
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
