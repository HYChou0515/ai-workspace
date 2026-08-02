"""Check a third-party artifact before its URL goes into an app (#674).

A human runs this with the URL an author has just handed them:

    python -m workspace_app.tooling verify <manifest-url> --name wafer-history

It answers one question — would this platform accept it? — and everything it
checks is something that would otherwise be found out after a release, by a
user, in a workspace.

**It does not execute the bundle.** The author's build already ran `smoke`
inside the sandbox's own base image and refuses to publish without it, so the
"does it actually run" question is answered where it can be answered properly.
Re-running it here would mean executing a stranger's code on an operator's
machine, in the wrong environment, to learn less.

What it does check is everything reachable without running anything: that the
URL resolves, that the artifact was built by a builder this platform accepts,
that the bytes are whole, and that the bundle's contents agree with the
manifest published beside it.
"""

from __future__ import annotations

import io
import json
import platform
import tarfile
from collections.abc import Callable
from dataclasses import dataclass

from workspace_app.tooling import grant as grant_policy
from workspace_app.tooling.artifact import (
    ArtifactError,
    artifact_opener,
    check_compatible,
    credential_for,
    parse_manifest,
    verify_bundle,
)
from workspace_app.tooling.builder import BUNDLE_NAME, MANIFEST_NAME

Fetcher = Callable[[str], bytes]


class VerifyFailed(ArtifactError):
    """This artifact would be refused. The message says which check and why."""


@dataclass(frozen=True)
class VerifyReport:
    """What would be registered if this URL were accepted."""

    name: str
    version: str
    sha: str
    commands: tuple[str, ...]
    #: Who on the platform team signed the certificate that let this artifact
    #: weigh what it does, or None when it is inside the default limit and no
    #: certificate bore on the decision. Read from the KEY that signed, so it
    #: is the one thing about a certificate nobody can assert about themselves.
    granted_by: str | None = None


def _bundle_url(manifest_url: str) -> str:
    head, _, tail = manifest_url.partition("?")
    if not head.endswith(MANIFEST_NAME):
        raise VerifyFailed(f"a tool URL must point at {MANIFEST_NAME}: {manifest_url}")
    swapped = head[: -len(MANIFEST_NAME)] + BUNDLE_NAME
    return f"{swapped}?{tail}" if tail else swapped


def _http_get(url: str) -> bytes:
    import urllib.request

    request = urllib.request.Request(url)  # noqa: S310 - operator-supplied https URL
    # The same rule the host and the runner apply. An operator typed this URL,
    # which makes it likelier to be right and no less able to be wrong — and
    # the credential here is the PLATFORM's, not one person's.
    token = credential_for(url)
    if token:
        request.add_header("PRIVATE-TOKEN", token)
    with artifact_opener().open(request, timeout=120) as response:  # noqa: S310
        return response.read()


def _fetch(fetch: Fetcher, url: str, what: str) -> bytes:
    try:
        return fetch(url)
    except OSError as exc:
        raise VerifyFailed(f"{what} is unreachable ({url}): {exc}") from exc


def verify_artifact(
    manifest_url: str,
    *,
    expected_name: str,
    builder: str,
    arch: str | None = None,
    fetch: Fetcher = _http_get,
) -> VerifyReport:
    """Raise `VerifyFailed` if this platform would refuse the artifact; else
    report what registering it would add."""
    # Shape first, before any network: telling an operator "that is not a
    # manifest URL" beats making them wait for a fetch that was never going
    # to work and then reporting it as unreachable.
    bundle_at = _bundle_url(manifest_url)
    raw = _fetch(fetch, manifest_url, "the manifest")
    try:
        manifest = parse_manifest(raw)
        check_compatible(manifest, builder=builder, arch=arch or platform.machine())
    except ArtifactError as exc:
        raise VerifyFailed(str(exc)) from exc

    # Admission before anything else about the artifact: pasting a URL into an
    # app is not the approval — a certificate the platform signed is, and it
    # is what says which tool this is.
    refusal = grant_policy.admit(
        tool=expected_name,
        url=manifest_url,
        token=manifest.grant,
        public_keys=grant_policy.TRUSTED_KEYS,
    )
    if refusal is not None:
        raise VerifyFailed(refusal)

    # Before the bundle is fetched: the size is published in the manifest so
    # that refusing an oversized artifact costs one small request, not the
    # download of the thing being refused.
    _check_weight(manifest)

    data = _fetch(fetch, bundle_at, "the bundle")
    try:
        verify_bundle(data, manifest.bundle)
    except ArtifactError as exc:
        raise VerifyFailed(str(exc)) from exc

    _check_contents(data, manifest)
    return VerifyReport(
        name=manifest.name,
        version=manifest.version,
        sha=manifest.bundle.sha256,
        commands=tuple(c.name for c in manifest.commands),
        granted_by=_granted_by(manifest),
    )


def _today():  # pragma: no cover - trivial, seamed so expiry is testable
    from datetime import date

    return date.today()


def _check_weight(manifest) -> None:
    """Refuse an artifact heavier than its tool is allowed to be.

    The author's build applies the same rule, on the same module. That check
    runs on a machine the author owns, so it is there to tell them early; this
    one is what the platform actually accepts.

    Nameless, like the author's build: which tool this is was settled by
    `admit`, and asking a second time against a second name is how a
    certificate ends up valid at one gate and refused at the next."""
    reason = grant_policy.check_size(
        size=manifest.bundle.size,
        token=manifest.grant,
        public_keys=grant_policy.TRUSTED_KEYS,
        today=_today(),
    )
    if reason is not None:
        raise VerifyFailed(
            f"{reason} — either the tool sheds weight, or it is reviewed and "
            "issued a certificate raising the limit"
        )


def _granted_by(manifest) -> str | None:
    """Who admitted this tool. Asked only after the gates have passed, so the
    certificate is known good by the time its issuer is read off it."""
    if manifest.grant is None:
        return None
    return grant_policy.read(manifest.grant, public_keys=grant_policy.TRUSTED_KEYS).issued_by


def _check_contents(data: bytes, manifest) -> None:
    """The bundle must actually contain the commands its manifest publishes.

    A mismatch means the schemas were frozen from a different build than the
    tree beside them — the model would be handed a tool that is not there. The
    author's smoke catches this at build time; checking again costs one
    comparison and catches anything that reached the store another way."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            listed = tar.extractfile("commands.json")
            if listed is None:
                raise VerifyFailed("the bundle has no commands.json — it is not a tool bundle")
            in_bundle = [entry["name"] for entry in json.loads(listed.read())]
    except (tarfile.TarError, KeyError, ValueError) as exc:
        raise VerifyFailed(f"the bundle could not be read: {exc}") from exc

    published = [c.name for c in manifest.commands]
    if in_bundle != published:
        raise VerifyFailed(
            f"the bundle contains {in_bundle} but its manifest publishes {published} — "
            "the schemas were frozen from a different build"
        )


_USAGE = "usage: python -m workspace_app.tooling.verify <manifest-url> --name <local name>"


def main(argv: list[str]) -> int:
    """The operator-facing command. Exit 0 = this platform would accept it.

    The BUILDER identity comes from the environment rather than a flag: it must
    be the one this deployment actually runs, and a flag would let a hurried
    operator "fix" a rejection by passing whatever the artifact claims."""
    import os
    import sys

    if len(argv) != 3 or argv[1] != "--name":
        print(_USAGE, file=sys.stderr)
        return 2
    builder = os.environ.get("TOOL_BUILDER_ID")
    if not builder:
        print(
            "TOOL_BUILDER_ID is not set — without the builder identity this "
            "deployment runs, there is nothing to check the artifact's ABI against",
            file=sys.stderr,
        )
        return 2
    try:
        report = verify_artifact(argv[0], expected_name=argv[2], builder=builder)
    except ArtifactError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    granted = f", size granted by {report.granted_by}" if report.granted_by else ""
    print(
        f"accepted: {report.name} {report.version} "
        f"({', '.join(report.commands) or 'no commands'}) sha256={report.sha}{granted}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    import sys

    raise SystemExit(main(sys.argv[1:]))
