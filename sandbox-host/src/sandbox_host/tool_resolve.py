"""Turn a tool's artifact URL into a bundle this host can mount (#674).

The app never reaches GitLab. It hands the host a name and a URL; the host
fetches, gates, installs and answers with the sha it installed plus the
command metadata. That split does two jobs at once:

* **The credential lives in one place.** Only the host needs to reach the
  artifact store, so only the host holds a token for it.
* **The schema and the bundle come from one act.** The app builds the model's
  tool definitions from THIS answer, and the sandbox later mounts THIS sha. If
  the app read the manifest separately, an author publishing between the two
  reads would leave the model calling a tool with the previous release's
  arguments — a failure that looks like the tool is broken and reproduces for
  nobody.

Fetching is stdlib `urllib`, not httpx: the host's dependency list is short on
purpose (see its pyproject), and this is one authenticated GET.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .artifact import (
    ArtifactError,
    CommandSpec,
    IncompatibleArtifact,
    check_compatible,
    parse_manifest,
    verify_bundle,
)
from .grant import admit
from .tool_cache import ToolCache

logger = logging.getLogger(__name__)

#: The names an author's CI publishes. Contract, not convention — the platform
#: is given one URL and derives the other.
MANIFEST_NAME = "tool.manifest.json"
BUNDLE_NAME = "tool.tar.gz"

#: Where the artifact store's credential comes from. One token for the whole
#: host; per-project tokens are a later problem than the first tool.
TOKEN_ENV = "TOOL_ARTIFACT_TOKEN"

# A seam for the network: the default performs the real GET; tests inject a
# stand-in so the rest of the behaviour is exercised without a server.
Fetcher = Callable[[str], bytes]


class FetchError(ArtifactError):
    """The artifact could not be retrieved. Distinct from "retrieved and
    refused" because the operator's next move is completely different."""


def bundle_url(manifest_url: str) -> str:
    """The bundle that sits beside a manifest.

    Swaps the last path segment only. GitLab's artifact endpoint carries the
    job name in a query parameter, so replacing the filename across the whole
    URL would corrupt it the moment a job is named after the file."""
    parts = urlsplit(manifest_url)
    head, _, tail = parts.path.rpartition("/")
    if tail != MANIFEST_NAME:
        raise FetchError(f"a tool URL must point at {MANIFEST_NAME}, this one ends in {tail!r}")
    return urlunsplit(parts._replace(path=f"{head}/{BUNDLE_NAME}"))


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url)  # noqa: S310 - https URLs from config
    token = os.environ.get(TOKEN_ENV)
    if token:
        # GitLab accepts either header; PRIVATE-TOKEN is the one that works for
        # both personal and project access tokens.
        request.add_header("PRIVATE-TOKEN", token)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        hint = (
            " — a GitLab artifact expires ~30 days after its build unless the job "
            "sets `expire_in: never`"
            if exc.code == 404
            else ""
        )
        raise FetchError(f"{url} answered {exc.code} {exc.reason}{hint}") from exc
    except OSError as exc:
        raise FetchError(f"{url} is unreachable: {exc}") from exc


@dataclass(frozen=True)
class ResolvedTool:
    """What the app needs to define this tool for the model, and what the
    sandbox needs to mount it."""

    name: str
    sha: str
    version: str
    commands: tuple[CommandSpec, ...]
    stale: bool
    """True when the artifact store could not be reached and this is the last
    version that resolved. The turn still runs; the app can say so."""


class ToolResolver:
    """Resolves artifact URLs against this host's cache."""

    def __init__(
        self,
        cache: ToolCache,
        *,
        builder_id: str,
        arch: str,
        fetch: Fetcher = _http_get,
        state_dir: Path,
        serve_last_known_good: bool = True,
    ) -> None:
        self._cache = cache
        #: Exposed so the host's sweeper can reclaim what nothing is running.
        self.cache = cache
        self._builder_id = builder_id
        self._arch = arch
        self._fetch = fetch
        self._state = state_dir / "last-known-good.json"
        #: The host serves a remembered version through an outage, because one
        #: artifact store being unreachable should not stop every workspace.
        #:
        #: A single-user runner turns this OFF, and the reason is revocation:
        #: access to a tool is managed where the artifact lives, so taking
        #: someone's read access away has to stop them running it. A resolver
        #: that fell back would keep serving the copy it already had for as
        #: long as that machine lived, somewhere nothing we do can reach.
        #: The status code cannot make the decision instead — GitLab answers
        #: 404 both for an expired artifact and for a project you may not see.
        self._serve_last_known_good = serve_last_known_good

    def resolve(self, name: str, manifest_url: str) -> ResolvedTool:
        """Install (or confirm) the tool at this URL and describe it.

        Raises `ArtifactError` when the tool cannot be made available at all."""
        try:
            manifest = parse_manifest(self._fetch(manifest_url))
        except (FetchError, OSError) as exc:
            # OSError too: the fetcher is any callable that does IO, and a
            # socket error escaping it un-wrapped is precisely the outage the
            # fallback exists for. Being strict here would fail the turn in
            # the one case we built a last-known-good copy to survive.
            return self._fall_back(name, manifest_url, exc)

        check_compatible(manifest, builder=self._builder_id, arch=self._arch)

        # Every third-party tool needs a certificate the platform signed, and
        # it is what says which tool this is — so a URL cannot be admitted by
        # having been pasted somewhere, and two authors' identically-named
        # commands do not collide.
        refusal = admit(tool=name, url=manifest_url, token=manifest.grant)
        if refusal is not None:
            raise IncompatibleArtifact(refusal)

        if not self._cache.has(manifest.bundle.sha256):
            data = self._fetch(bundle_url(manifest_url))
            verify_bundle(data, manifest.bundle)
            self._cache.ensure(manifest.bundle.sha256, data)

        self._remember(manifest_url, manifest)
        return ResolvedTool(
            name=name,
            sha=manifest.bundle.sha256,
            version=manifest.version,
            commands=manifest.commands,
            stale=False,
        )

    def _fall_back(self, name: str, url: str, cause: Exception) -> ResolvedTool:
        """Serve the last version that resolved, rather than failing the turn.

        An artifact store outage should not take every workspace with it — but
        the answer is marked `stale` so nobody mistakes it for the latest."""
        remembered = self._recall().get(url)
        if (
            not self._serve_last_known_good
            or remembered is None
            or not self._cache.has(remembered["sha"])
        ):
            raise FetchError(f"cannot resolve {name}: {cause}") from cause
        logger.warning("tool %s: serving last known good %s (%s)", name, remembered["sha"], cause)
        return ResolvedTool(
            name=name,
            sha=remembered["sha"],
            version=remembered["version"],
            commands=tuple(
                CommandSpec(
                    name=c["name"],
                    description=c["description"],
                    params_json_schema=c["params_json_schema"],
                )
                for c in remembered["commands"]
            ),
            stale=True,
        )

    def _recall(self) -> dict[str, dict]:
        try:
            return json.loads(self._state.read_text("utf-8"))
        except (OSError, ValueError):
            # A missing or corrupt note is not an error: it only means there is
            # nothing to fall back to.
            return {}

    def _remember(self, url: str, manifest) -> None:
        known = self._recall()
        known[url] = {
            "sha": manifest.bundle.sha256,
            "version": manifest.version,
            "commands": [
                {
                    "name": c.name,
                    "description": c.description,
                    "params_json_schema": c.params_json_schema,
                }
                for c in manifest.commands
            ],
        }
        self._state.parent.mkdir(parents=True, exist_ok=True)
        self._state.write_text(json.dumps(known), "utf-8")
