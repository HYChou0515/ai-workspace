"""Is the sandbox host running the code we think it is?

In production the sandbox is a SEPARATE service with its own image and its own
deploy (`sandbox.kind: http`). An old one answers every request perfectly well —
it just behaves like the old code — so nothing else in the app can tell the
difference: a tool that needs `$HOME` fails inside the sandbox while every other
health signal stays green. That ambiguity has cost two full diagnoses.

The host advertises its capabilities on `/healthz`; this probe compares them
against what the app needs and, when something is missing, says so in the terms
that lead somewhere: the image is behind, rebuild and roll it out. The app's own
code being fine is exactly what makes this worth stating out loud.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from ..protocol import CheckResult, ISanityCheck

# Behaviours the app relies on the host having. Add one here when the app starts
# depending on it, and add the matching string in `sandbox_host.app` — a name
# that is never advertised reads as a permanently stale host.
REQUIRED_CAPABILITIES = frozenset(
    {
        # Every exec gets HOME pointed at the sandbox's own `.home`, created and
        # owned to the exec uid at that moment. Without it, `soffice` (and
        # anything else writing a profile to $HOME) fails in the sandbox with
        # "User installation could not be completed".
        "per-exec-home",
    }
)


class SandboxHostCapabilityCheck(ISanityCheck):
    # Not in the startup fast set: that runs synchronously, and at boot the host
    # may legitimately not be up yet — an `error` there would be noise, not news.
    # This belongs to the diagnostics round the operator triggers.
    fast = False
    check_id = "sandbox-host-capabilities"
    description = "The sandbox host's image carries the behaviours this app expects"

    def __init__(
        self,
        *,
        base_url: str,
        required: frozenset[str] = REQUIRED_CAPABILITIES,
        timeout_s: float = 3.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._required = required
        self._timeout_s = timeout_s
        self._client_factory = client_factory

    def _client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=self._timeout_s)

    def run(self) -> CheckResult:
        # `kind: local` / mock has no host to interrogate. Skip rather than fail,
        # so a laptop's diagnostics page doesn't cry wolf.
        if not self._base_url:
            return CheckResult(
                check_id=self.check_id,
                status="skip",
                detail="sandbox.kind is not http — no separate host to check",
            )
        try:
            with self._client() as client:
                resp = client.get(f"{self._base_url}/healthz", timeout=self._timeout_s)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001 — any transport problem is `error`
            # Wiring, not behaviour: `error` points the reader at connectivity
            # instead of at the image.
            return CheckResult(
                check_id=self.check_id,
                status="error",
                detail=f"cannot reach the sandbox host at {self._base_url}: {exc}",
            )

        advertised = set(body.get("capabilities") or ())
        version = str(body.get("version") or "unknown")
        missing = sorted(self._required - advertised)
        if not missing:
            return CheckResult(
                check_id=self.check_id,
                status="pass",
                detail=f"host {version} advertises {len(advertised)} capabilities",
            )
        # A host predating the advertisement sends no `capabilities` at all; the
        # absence IS the signal, so it lands here rather than passing by default.
        return CheckResult(
            check_id=self.check_id,
            status="fail",
            detail=(
                f"the sandbox host (version {version}) is missing {', '.join(missing)} — "
                f"its image is behind this app. Rebuild the sandbox-host image and roll it "
                f"out; the app's own code cannot compensate for what the host does not do."
            ),
        )
