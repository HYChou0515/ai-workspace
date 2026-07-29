"""Executable confirmation that each phase of `docs/plan-third-party-tools.md` landed (#674).

    python3 scripts/check_third_party_tools_674.py


Each check names the phase and asserts something concrete about the tree —
a file, a symbol, a wiring — rather than trusting a summary.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

results: list[tuple[str, bool, str]] = []


def check(phase: str, what: str, ok: bool) -> None:
    results.append((phase, ok, what))


def text(rel: str) -> str:
    return (REPO / rel).read_text("utf-8")


def exists(rel: str) -> bool:
    return (REPO / rel).exists()


# ---- P1: artifact contract -------------------------------------------------
a = text("src/workspace_app/tooling/artifact.py")
check(
    "P1",
    "artifact.py exists with the four contract functions",
    all(
        f"def {n}(" in a
        for n in ("parse_manifest", "check_compatible", "verify_bundle", "render_manifest")
    ),
)
check(
    "P1",
    "gates raise distinct, catchable errors",
    all(f"class {n}(" in a for n in ("ManifestError", "IncompatibleArtifact", "ChecksumMismatch")),
)
check(
    "P1",
    "the module is stdlib-only, so the builder can import it",
    not re.search(
        r"^from (?!__future__|dataclasses|collections|typing)\w|^import (?!hashlib|json)", a, re.M
    ),
)
check(
    "P1",
    "the host carries a byte-identical copy",
    (REPO / "sandbox-host/src/sandbox_host/artifact.py").read_bytes()
    == (REPO / "src/workspace_app/tooling/artifact.py").read_bytes(),
)

# ---- P2: builder image -----------------------------------------------------
b = text("src/workspace_app/tooling/builder.py")
check("P2", "build_artifact + smoke exist", "def build_artifact(" in b and "def smoke(" in b)
check(
    "P2",
    "smoke runs inside build and a failure removes the output",
    "(smoke_check or smoke)(out)" in b and "shutil.rmtree(out" in b,
)
check(
    "P2",
    "builder image exists and pins the ABI anchor",
    exists("tool-builder/Dockerfile")
    and "TOOL_BUILDER_ID=${BUILDER_ID}" in text("tool-builder/Dockerfile"),
)
check(
    "P2",
    "builder base == sandbox runtime base",
    re.findall(r"^FROM\s+(\S+)", text("tool-builder/Dockerfile"), re.M)[-1]
    == re.findall(r"^FROM\s+(\S+)", text("sandbox-host/Dockerfile"), re.M)[-1],
)
check(
    "P2",
    "both commands ship in the image",
    exists("tool-builder/bin/build-tool") and exists("tool-builder/bin/smoke"),
)

# ---- P3: author docs -------------------------------------------------------
check(
    "P3",
    "tool-authoring.md exists and is in the nav",
    exists("docs/tool-authoring.md") and "tool-authoring.md" in text("mkdocs.yml"),
)
check(
    "P3",
    "the CI template pins artifacts against expiry",
    "expire_in: never" in text("tool-builder/gitlab-ci.example.yml"),
)
check(
    "P3",
    "the doc states the limits an author cannot see",
    all(s in text("docs/tool-authoring.md") for s in ("截斷", "時間上限", "expire_in: never")),
)

# ---- P4: cache -------------------------------------------------------------
c = text("sandbox-host/src/sandbox_host/tool_cache.py")
check("P4", "content-addressed cache with a sha guard", "_SHA256" in c and "def ensure(" in c)
check("P4", "extraction uses the safe tar filter", 'filter="data"' in c)
check("P4", "install is atomic (staged, then renamed)", "staging.rename(installed)" in c)
check("P4", "the tree is hardened root-owned", "os.chown(path, 0, 0)" in c)

# ---- P5: builtin layout ----------------------------------------------------
check("P5", "the layout constants exist", "BUILTIN_DIR" in c and "EXT_DIR" in c)
check(
    "P5",
    "the host image ships first-party tools under builtin/",
    "/opt/tools/builtin" in text("sandbox-host/Dockerfile"),
)
check(
    "P5",
    "the sandbox resolves builtin under the tools root",
    "_builtin_tools" in text("sandbox-host/src/sandbox_host/local_process.py"),
)

# ---- P6: resolve endpoint --------------------------------------------------
r = text("sandbox-host/src/sandbox_host/tool_resolve.py")
check(
    "P6",
    "resolver fetches, gates, verifies and installs",
    all(s in r for s in ("parse_manifest", "check_compatible", "verify_bundle", "cache.ensure")),
)
check(
    "P6",
    "last-known-good fallback exists and is flagged stale",
    "_fall_back" in r and "stale=True" in r,
)
check(
    "P6",
    "the endpoint is registered and answers partially",
    '"/tools/resolve"' in text("sandbox-host/src/sandbox_host/app.py")
    and '"refused"' in text("sandbox-host/src/sandbox_host/app.py"),
)
check("P6", "the wire doc documents it", "/tools/resolve" in text("docs/sandbox-host-wire.md"))
check(
    "P6",
    "a host that cannot gate builds no resolver",
    "build_tool_resolver" in text("sandbox-host/src/sandbox_host/service.py"),
)

# ---- P7: per-sandbox view --------------------------------------------------
lp = text("sandbox-host/src/sandbox_host/local_process.py")
check(
    "P7",
    "SandboxSpec carries {name: sha}",
    "tools: dict[str, str] | None" in text("sandbox-host/src/sandbox_host/protocol.py"),
)
check("P7", "the per-sandbox view is assembled", "_build_tools_view" in lp and "_TOOLS_VIEW" in lp)
check(
    "P7",
    "jailed mounts each tool read-only and seals the view",
    'mount -o remount,bind,ro "$ROOT/.tools/$n"' in lp
    and 'mount -o remount,ro -t tmpfs tmpfs "$ROOT/.tools"' in lp,
)

# ---- P8: app wiring --------------------------------------------------------
check(
    "P8",
    "app.json can declare external tools",
    "external_tools" in text("src/workspace_app/apps/manifest.py"),
)
check(
    "P8",
    "the app resolves once per turn and pins the shas",
    "_external_tools" in text("src/workspace_app/api/turn_context.py")
    and "SandboxSpec(tools=external.shas or None)" in text("src/workspace_app/api/turn_context.py"),
)
check(
    "P8",
    "resolved tools become packages the model can call",
    "packages=[*(self._packages or []), *external.packages]"
    in text("src/workspace_app/api/turn_context.py"),
)
check(
    "P8",
    "the client sends tools on create and can resolve",
    '"tools": spec.tools' in text("src/workspace_app/sandbox/http_client.py")
    and "async def resolve_tools(" in text("src/workspace_app/sandbox/http_client.py"),
)
check(
    "P8",
    "unavailable tools are disclosed to the model",
    "format_unavailable_tools_for_prompt" in text("src/workspace_app/agent/tool_prompt.py")
    and "unavailable_tools" in text("src/workspace_app/api/litellm_runner.py"),
)

# ---- P9: verify + prewarm --------------------------------------------------
v = text("src/workspace_app/tooling/verify.py")
check(
    "P9",
    "verify checks shape, gate, integrity and contents",
    all(s in v for s in ("_bundle_url", "check_compatible", "verify_bundle", "_check_contents")),
)
check("P9", "the builder id cannot be passed as a flag", 'os.environ.get("TOOL_BUILDER_ID")' in v)
check(
    "P9",
    "boot warms the cache without gating readiness",
    "prewarm_tools" in text("src/workspace_app/api/lifecycle.py")
    and "prewarm_external_tools" in text("src/workspace_app/api/app.py"),
)

# ---- P10: GC ---------------------------------------------------------------
check(
    "P10",
    "the sweep exists and honours in_use absolutely",
    "def sweep(" in c and "if path.name in in_use:" in c,
)
check("P10", "unreferenced bundles are kept while there is room", "max_bytes is None" in c)
check(
    "P10",
    "the ceiling is configurable and swept on the reaper tick",
    "tool_cache_max_bytes" in text("sandbox-host/src/sandbox_host/config.py")
    and "sweep_tool_cache" in text("sandbox-host/src/sandbox_host/__main__.py"),
)

# ---- P11: docs -------------------------------------------------------------
e = text("docs/extending-the-platform.md")
check("P11", "the old dev-only stance is gone", "## Tool（只有 dev 自建）" not in e)
check(
    "P11",
    "both paths are documented side by side",
    "第一方（vendor 進 repo）" in e and "tool-authoring.md" in e,
)
check(
    "P11",
    "operations covers token, disk and rollback",
    all(
        s in text("docs/deployment.md")
        for s in (
            "TOOL_BUILDER_ID",
            "TOOL_ARTIFACT_TOKEN",
            "SANDBOX_HOST_TOOL_CACHE_MAX_BYTES",
            "退回",
        )
    ),
)

# ---- P12: invariants + e2e -------------------------------------------------
check(
    "P12",
    "an end-to-end test walks artifact -> resolve -> sandbox -> run",
    exists("sandbox-host/tests/test_third_party_end_to_end.py"),
)
check(
    "P12",
    "version isolation is asserted",
    "leaves_the_running_one_alone" in text("sandbox-host/tests/test_third_party_end_to_end.py"),
)
check(
    "P12",
    "the view is asserted never uid-owned",
    "never_handed_to_the_sandboxs_own_uid" in text("sandbox-host/tests/test_isolated_process.py"),
)
check(
    "P12",
    "a root-gated test proves the real permissions",
    "root_owned_and_a_sandbox_cannot_write_it"
    in text("sandbox-host/tests/test_isolated_process_integration.py"),
)

# ---- report ----------------------------------------------------------------
failed = [(p, w) for p, ok, w in results if not ok]
for phase, ok, what in results:
    print(f"{'PASS' if ok else 'FAIL'}  {phase}  {what}")
print(f"\n{len(results) - len(failed)}/{len(results)} checks pass")
sys.exit(1 if failed else 0)
