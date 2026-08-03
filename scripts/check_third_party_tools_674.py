"""Executable confirmation that each phase of `docs/plan-third-party-tools.md` landed (#674).

    python3 scripts/check_third_party_tools_674.py


Each check names the phase and asserts something concrete about the tree —
a file, a symbol, a wiring — rather than trusting a summary.
"""

from __future__ import annotations

import re
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
        r"^from (?!__future__|dataclasses|collections|typing|urllib)\w"
        r"|^import (?!hashlib|json|os|urllib)",
        a,
        re.M,
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
    # The template moved into the starter folder we hand an author, so it
    # arrives as a runnable `.gitlab-ci.yml` rather than an example to copy.
    "expire_in: never" in text("tool-starter/.gitlab-ci.yml"),
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
check(
    "P4",
    "the tree is hardened root-owned, without following a link",
    "os.lchown(path, 0, 0)" in c and "os.chown(" not in c,
)

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
    # `or None` was deliberately dropped in e2941027 — an empty mapping was
    # the second way of spelling "no third-party tools".
    and "SandboxSpec(tools=external.shas)" in text("src/workspace_app/api/turn_context.py"),
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
    # `bundle_url` moved into the shared contract: `verify` and the host used
    # to disagree about what a manifest URL is, which is how an operator got
    # `accepted:` and then a release where every resolve failed.
    all(s in v for s in ("bundle_url", "admit", "verify_bundle", "_check_contents"))
    and "def bundle_url(" in a,
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

# ---- P13: dev dependencies stay out of the bundle -------------------------
pre = text("src/workspace_app/tooling/prebuild.py")
check("P13", "uv sync is told to leave the dev group out", '"--no-dev",' in pre)
check(
    "P13",
    "a real build proves the bundle is clean, not just the argv",
    "test_a_dev_only_dependency_never_reaches_the_bundle" in text("tests/tooling/test_prebuild.py"),
)

# ---- P14: the size limit and its escape hatch -----------------------------
g = text("src/workspace_app/tooling/grant.py")
check("P14", "the limit is 150MB on the compressed artifact", "150 * 1024 * 1024" in g)
check(
    "P14",
    "one rule, called by both the build and the gate",
    "def check_size(" in g
    and "grant_policy.check_size(" in text("src/workspace_app/tooling/builder.py")
    and "grant_policy.check_size(" in text("src/workspace_app/tooling/verify.py"),
)
check(
    "P14",
    "certificates are ed25519, and the key list allows rotation",
    "ed25519" in g and "TRUSTED_KEYS" in g,
)
check("P14", "a certificate names one tool", "was issued for" in g)
check(
    "P14",
    "a raised allowance has to carry the deadline that ends it",
    "--publish-until is required" in g,
)
check(
    "P14",
    "a certificate admits one tool, from one place",
    "def admit(" in g and "granted.source" in g,
)
check(
    "P14",
    "names are not handed out twice",
    "REGISTRY_FILE" in g and "already issued to" in g,
)
check(
    "P14",
    "the credential is one rule, shared by all three fetchers",
    "def credential_for(" in text("src/workspace_app/tooling/artifact.py")
    and "credential_for(url)" in text("src/workspace_app/tooling/verify.py")
    and "credential_for(url)" in text("sandbox-host/src/sandbox_host/tool_resolve.py"),
)
check(
    "P14", "the signing key is created 0600 and never overwritten", "O_EXCL" in g and "0o600" in g
)
check(
    "P14",
    "the certificate is only consulted above the default limit",
    "test_a_lapsed_certificate_does_not_fail_a_tool_that_no_longer_needs_one"
    in text("tests/tooling/test_grant.py"),
)
check(
    "P14",
    "the refusal names what accounts for the weight",
    "def _heaviest(" in text("src/workspace_app/tooling/builder.py"),
)
check(
    "P14",
    "the gate refuses before downloading the bundle",
    "test_an_oversized_artifact_is_refused_without_downloading_it"
    in text("tests/tooling/test_verify.py"),
)
check(
    "P14",
    "the certificate travels in the manifest, both copies of the contract",
    'grant=body.get("grant")' in text("src/workspace_app/tooling/artifact.py")
    and 'grant=body.get("grant")' in text("sandbox-host/src/sandbox_host/artifact.py"),
)
check(
    "P14",
    "the builder image installs exactly what the build path imports",
    "uv pip install --system --no-cache cryptography" in text("tool-builder/Dockerfile")
    and "test_the_builder_image_installs_exactly_what_the_build_path_imports"
    in text("tests/tooling/test_builder_image.py"),
)
check(
    "P14",
    "authors are told the limit and how to ask for more",
    "150MB" in text("tool-starter/README.md")
    and "tool-certificate.token" in text("tool-starter/README.md")
    and "tool-certificate.token" in text("tool-starter/CLAUDE.md")
    and "tool-certificate.token" in text("docs/tool-authoring.md"),
)
check(
    "P14",
    "the platform team is told how to issue one, and both ways to take one back",
    "grant keygen" in text("docs/deployment.md")
    # A certificate is checked offline, so neither removal is a thing you do
    # TO it — the doc has to name what you do instead.
    and "改不到它" in text("docs/deployment.md")
    and "從 `app.json` 拿掉" in text("docs/deployment.md")
    and "從 `TRUSTED_KEYS` 拿掉" in text("docs/deployment.md"),
)

# ---- P15: one MCP runner, not an image per tool ---------------------------
runner = text("sandbox-host/src/sandbox_host/mcp_runner.py")
check(
    "P15",
    "the runner resolves through the same machinery the host uses",
    "ToolResolver" in runner and "ToolCache" in runner,
)
check("P15", "it becomes the tool's own server rather than wrapping it", "os.execv" in runner)
check(
    "P15",
    "nothing but the protocol reaches stdout",
    "test_nothing_reaches_stdout_before_the_handover"
    in text("sandbox-host/tests/test_mcp_runner.py"),
)
check(
    "P15",
    "the runner image exists and is ABI-anchored like the others",
    "TOOL_BUILDER_ID=${BUILDER_ID}" in text("sandbox-host/mcp-runner.Dockerfile"),
)
check(
    "P15",
    "the per-tool image path is gone, not merely unused",
    "DOCKERFILE_NAME" not in text("src/workspace_app/tooling/builder.py")
    and "package-mcp" not in text("tool-starter/.gitlab-ci.yml"),
)
check(
    "P15",
    "authors and operators are told how the second path now works",
    "runner" in text("tool-starter/README.md") and "mcp-runner" in text("docs/deployment.md"),
)

# ---- P16: the skill someone installs to start from nothing ---------------
skill = text("tool-skill/SKILL.md")
check("P16", "the skill exists and names itself", "name: install-workspace-tool" in skill)
check(
    "P16",
    "the one fact it cannot derive is a placeholder, and it refuses to guess",
    "<<RUNNER_IMAGE>>" in skill and "do not guess" in skill.lower(),
)
check(
    "P16",
    "the ambiguous 404 is separated for the reader",
    "expire_in: never" in skill and "browser" in skill.lower(),
)
check(
    "P16",
    "every failure names which of the three parties fixes it",
    all(p in skill for p in ("tool's author", "platform team", "The person")),
)
check(
    "P16",
    "an install is not reported without being run",
    "tools/list" in skill and "without step 3" in skill,
)
check(
    "P16",
    "what the runner prints is checked against the skill by running it",
    "test_the_skill_explains_the_warning_this_runner_prints"
    in text("sandbox-host/tests/test_mcp_runner.py"),
)
check(
    "P16",
    "the operator is told the one line to fill in",
    "<<RUNNER_IMAGE>>" in text("tool-skill/README.md")
    and "tool-skill" in text("docs/deployment.md"),
)

# ---- P17/P18: admission, uniqueness, and where a credential may go -------
check(
    "P17",
    "identity left the manifest's name for the certificate",
    "expected_name" not in text("src/workspace_app/tooling/artifact.py")
    and "admit(tool=name" in text("sandbox-host/src/sandbox_host/tool_resolve.py"),
)
check(
    "P17",
    "the one deadline is the author's, and the host does not read it",
    "publish_until" in g
    and "publish_until" not in text("sandbox-host/src/sandbox_host/tool_resolve.py"),
)
check(
    "P18",
    "the images that fetch are told where the credential may go",
    all(
        "TOOL_ARTIFACT_HOSTS=${ARTIFACT_HOSTS}" in text(f"sandbox-host/{n}")
        for n in ("Dockerfile", "mcp-runner.Dockerfile")
    ),
)
check(
    "P18",
    "a tool does not inherit the credential that fetched it",
    "os.execve(" in text("sandbox-host/src/sandbox_host/mcp_runner.py"),
)
check(
    "P18",
    "the skill explains the refusals this added",
    all(
        s in text("tool-skill/SKILL.md")
        for s in ("carries no certificate", "TOOL_ARTIFACT_HOSTS", "is good for artifacts under")
    ),
)

# ---- report ----------------------------------------------------------------
failed = [(p, w) for p, ok, w in results if not ok]
for phase, ok, what in results:
    print(f"{'PASS' if ok else 'FAIL'}  {phase}  {what}")
print(f"\n{len(results) - len(failed)}/{len(results)} checks pass")
sys.exit(1 if failed else 0)
