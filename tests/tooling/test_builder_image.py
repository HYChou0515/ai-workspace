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
    # An author's CI should pull a small image. Installing the app would be
    # pure weight — and would drag litellm and a data-science stack into a
    # stranger's CI.
    text = (_REPO / "tool-builder" / "Dockerfile").read_text("utf-8")

    assert "uv sync" not in text
    assert "pyproject.toml" not in text


def _third_party_imports_reachable_from(entry: str) -> set[str]:
    """Every non-stdlib package the build path imports, followed through the
    tooling modules the image copies."""
    import ast
    import sys

    root = _REPO / "src" / "workspace_app" / "tooling"
    seen: set[str] = set()
    queue = [entry]
    third: set[str] = set()

    def note(dotted: str) -> None:
        top = dotted.split(".")[0]
        if dotted.startswith("workspace_app.tooling."):
            queue.append(dotted.split(".")[2])
        elif top not in sys.stdlib_module_names and top != "workspace_app":
            third.add(top)

    while queue:
        module = queue.pop()
        if module in seen:
            continue
        seen.add(module)
        source = root / f"{module}.py"
        if not source.exists():
            continue
        for node in ast.walk(ast.parse(source.read_text("utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    note(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                # `from workspace_app.tooling import grant` names the module
                # in the aliases, not in `node.module`.
                if node.module == "workspace_app.tooling":
                    queue += [alias.name for alias in node.names]
                else:
                    note(node.module)
    return third


def test_the_builder_image_installs_exactly_what_the_build_path_imports() -> None:
    """The image is what an author's CI runs, and it is assembled from a list
    written by hand. An import added to any module `build-tool` reaches would
    fail there rather than here — in a stranger's pipeline, on a build they
    cannot debug.

    Enforcing equality in both directions: a missing package breaks their
    build, and a package nothing imports is weight in every author's CI."""
    text = (_REPO / "tool-builder" / "Dockerfile").read_text("utf-8")
    installed = {
        package
        for line in re.findall(r"uv pip install[^\n]*", text)
        for package in line.split()[3:]
        if not package.startswith("-")
    }

    assert installed == _third_party_imports_reachable_from("builder")


def test_every_image_carries_the_same_abi_anchor_knob() -> None:
    # The gate compares the manifest's `builder` against the image's
    # TOOL_BUILDER_ID. If only some images took the value, third-party
    # artifacts would be refused in one place and accepted in another.
    for rel in (
        ("tool-builder", "Dockerfile"),
        ("sandbox-host", "Dockerfile"),
        ("sandbox-host", "mcp-runner.Dockerfile"),
    ):
        text = _REPO.joinpath(*rel).read_text("utf-8")
        assert "ARG BUILDER_ID" in text, rel
        assert "TOOL_BUILDER_ID=${BUILDER_ID}" in text, rel


def test_the_mcp_runner_is_built_on_the_base_the_bundles_expect() -> None:
    """The runner executes a third-party bundle directly, so it is bound by
    the same rule as everything else that does: the bundle's interpreter and
    native wheels only load on the base they were built against."""
    runner = _bases(_REPO / "sandbox-host" / "mcp-runner.Dockerfile")[-1]

    assert runner == _bases(_REPO / "tool-builder" / "Dockerfile")[-1]


def test_the_runner_leaves_no_orphan_volume_behind_when_no_cache_is_mounted() -> None:
    """Running without a cache mount is a supported choice — it fetches every
    start and keeps nothing. Declaring /cache as a VOLUME would quietly break
    that: docker hands each unmounted run an anonymous volume holding a whole
    unpacked bundle, and only `--rm` ever removes one.

    Measured, not assumed: two runs without `--rm` left two orphans behind."""
    text = (_REPO / "sandbox-host" / "mcp-runner.Dockerfile").read_text("utf-8")

    assert not re.search(r"^VOLUME\b", text, re.MULTILINE)


def test_the_ci_template_pins_artifacts_against_expiry() -> None:
    # R5. GitLab expires CI artifacts after ~30 days by default. Once the URL
    # 404s, hosts that already cached the tool limp on their copy while every
    # NEW host gets nothing — it presents as "it worked yesterday", which is
    # the worst kind of failure to hand a tool author.
    template = (_REPO / "tool-starter" / ".gitlab-ci.yml").read_text("utf-8")

    assert "expire_in: never" in template
    assert "build-tool" in template


def test_the_authoring_doc_states_the_limits_an_author_cannot_see() -> None:
    # An author who does not know about these ships a tool that looks fine in
    # their own testing and then truncates or times out in front of a user.
    doc = (_REPO / "docs" / "tool-authoring.md").read_text("utf-8")

    assert "截斷" in doc
    assert "時間上限" in doc
    assert "expire_in: never" in doc


def test_the_host_image_ships_first_party_tools_under_builtin() -> None:
    # #674 moved the tools root from "a directory of tools" to a layout:
    # builtin/ (baked in) beside ext/ (fetched, sha-keyed). The image and the
    # code that resolves `builtin/` have to agree, and nothing else would
    # notice if they stopped — the sandbox would simply come up with no tools.
    text = (_REPO / "sandbox-host" / "Dockerfile").read_text("utf-8")

    assert "/opt/tools/builtin" in text


def test_the_platform_docs_no_longer_say_tools_can_only_come_from_our_repo() -> None:
    # #674 changed the answer to "who can add a tool", and a doc still saying
    # "dev only, in our repo" would send an external author to the wrong place
    # — or stop them asking at all.
    doc = (_REPO / "docs" / "extending-the-platform.md").read_text("utf-8")

    assert "## Tool（只有 dev 自建）" not in doc
    assert "tool-authoring.md" in doc
    assert "external_tools" in doc


def test_the_deployment_docs_say_how_to_roll_a_third_party_tool_back() -> None:
    # The one operational question the design's "it updates itself" raises,
    # and the one an operator will need answered under pressure.
    doc = (_REPO / "docs" / "deployment.md").read_text("utf-8")

    assert "TOOL_BUILDER_ID" in doc
    assert "SANDBOX_HOST_TOOL_CACHE_MAX_BYTES" in doc
    assert "退回" in doc


def test_the_author_dev_host_keeps_the_privilege_that_makes_it_faithful() -> None:
    """Without `privileged`, the kernel refuses the jail and the host does not
    fail — it falls back to the unjailed path, where /.tools is a symlink
    rather than a read-only mount. A tool that writes next to itself then
    passes locally and breaks in production, which is precisely what this
    environment exists to catch. Removing the line degrades it silently, so
    the line is asserted."""
    compose = (_REPO / "tool-starter" / "compose.tool-dev.yaml").read_text("utf-8")

    assert "privileged: true" in compose
    # And the ABI anchor, without which third-party tools are simply disabled.
    assert "TOOL_BUILDER_ID" in compose


def test_the_author_dev_host_pulls_a_published_image_rather_than_guessing_one() -> None:
    # A hardcoded registry would be a guess that fails confusingly; a required
    # variable fails by naming itself.
    compose = (_REPO / "tool-starter" / "compose.tool-dev.yaml").read_text("utf-8")

    assert "${SANDBOX_HOST_IMAGE:?" in compose


def test_the_authoring_doc_is_honest_about_what_the_dev_host_cannot_reproduce() -> None:
    # An author who believes a local pass means production works has been given
    # false confidence — the failure mode this whole design keeps circling.
    doc = (_REPO / "docs" / "tool-authoring.md").read_text("utf-8")

    assert "privileged: true" in doc
    assert "不重現什麼" in doc
    assert "nginx" in doc


_STARTER = _REPO / "tool-starter"


def test_the_starter_never_reaches_into_the_platform() -> None:
    """The one rule an author cannot be allowed to break by copying us.

    An import of `workspace_app` means their tool cannot be tested without our
    package, and that we cannot change ours without breaking theirs. The
    dispatcher in the starter is hand-written for exactly this reason — which
    is worth nothing if someone later "simplifies" it by importing ours."""
    # Every file, not just `*.py`. The worst version of this coupling is one
    # line in `pyproject.toml` — a dependency on our package — which no scan
    # of the source would ever have seen.
    offenders = [
        path.relative_to(_STARTER)
        for path in _STARTER.rglob("*")
        if path.is_file()
        and ".venv" not in path.parts
        and path.name != "uv.lock"
        and "workspace_app" in path.read_text("utf-8", errors="ignore")
    ]

    assert not offenders, f"the starter must stand alone, but these reach into ours: {offenders}"


def test_the_starter_ships_exactly_one_entry_point() -> None:
    # The launcher runs one console script. Two would make "which command did
    # the model just run" unanswerable; the build refuses either way, and an
    # author should not discover that from a red CI job.
    import tomllib

    project = tomllib.loads((_STARTER / "pyproject.toml").read_text("utf-8"))

    assert len(project["project"]["scripts"]) == 1
    assert project["project"]["version"]
    assert (_STARTER / "uv.lock").is_file(), "the build refuses without a lock file"


def test_the_starters_agent_brief_interviews_before_it_writes() -> None:
    """A tool that nobody can describe in one sentence will not be called by a
    model, so the brief has to stop an agent from coding first. And it has to
    carry the rules, because an agent that does not know them writes something
    that passes locally and fails in front of a user."""
    brief = (_STARTER / "CLAUDE.md").read_text("utf-8")

    assert "Interview first" in brief
    assert "## 2. Rules" in brief
    assert "Stand alone" in brief  # the standing-alone rule, stated as a requirement
    assert "stdout" in brief  # the answer channel
    assert "read-only" in brief  # the bundle


def test_the_starter_shows_both_ways_to_write_a_command() -> None:
    """The decorator lives in the author's own `common.py`, so they get the
    ergonomics without importing ours — which is the whole reason ours is
    off limits. Showing both styles side by side is what makes that a
    choice rather than a restriction."""
    assert (_STARTER / "src" / "my_tool" / "common.py").is_file()
    commands = (_STARTER / "src" / "my_tool" / "commands").glob("*.py")
    bodies = {p.name: p.read_text("utf-8") for p in commands}

    assert "@command(" in bodies["head.py"]  # decorated
    assert "DESCRIPTION" in bodies["count.py"]  # spelled out
    assert "def run(" in bodies["count.py"]


def test_the_starter_teaches_the_exit_code_contract_where_an_author_will_see_it() -> None:
    """The platform reads these numbers to decide what the model does next, so
    an author who learns them late has already shipped a tool that reports its
    failures as unactionable. Both documents carry it, and the code raises it."""
    assert "Retryable" in (_STARTER / "src" / "my_tool" / "common.py").read_text("utf-8")
    assert "NeedsAction" in (_STARTER / "src" / "my_tool" / "common.py").read_text("utf-8")
    assert "Retryable" in (_STARTER / "CLAUDE.md").read_text("utf-8")
    assert "exit code" in (_STARTER / "README.md").read_text("utf-8")


def test_the_platform_docs_carry_the_exit_code_contract_too() -> None:
    """The starter teaches it, but a first-party tool goes through the same
    path and its author reads these pages instead. A contract documented in
    only one of the two places is one half of the tools getting it wrong."""
    authoring = (_REPO / "docs" / "tool-authoring.md").read_text("utf-8")

    for code in ("`2`", "`3`", "`124`", "`-9`"):
        assert code in authoring, f"{code} is part of the contract and belongs here"


def test_the_author_ci_publishes_the_artifact_and_stops_there() -> None:
    """An author's pipeline used to build and push a container image as well,
    which meant docker-in-docker, registry credentials and a second push in a
    stranger's CI — to store bytes their artifact already held.

    The MCP runner fetches any tool from its URL, so the same two files serve
    both paths and the author's CI has one job."""
    ci = (_STARTER / ".gitlab-ci.yml").read_text("utf-8")

    assert "package-mcp" not in ci
    assert "docker" not in ci.lower()
    assert "build-tool" in ci


def test_the_runner_is_the_only_way_a_tool_reaches_mcp() -> None:
    # Two ways to run the same tool would drift, and the one being removed
    # verified nothing at run time.
    assert not (_STARTER / "mcp.Dockerfile").exists()
    assert (_REPO / "sandbox-host" / "mcp-runner.Dockerfile").exists()


def test_the_engineer_facing_setup_mounts_their_working_directory() -> None:
    # Tools resolve paths against the working directory. Without the mount an
    # engineer's first call returns "no such file" for a file they can see.
    readme = (_STARTER / "README.md").read_text("utf-8")

    assert "mcpServers" in readme
    assert "/work" in readme


def test_the_grant_runbook_says_the_name_is_ours_to_choose() -> None:
    """It used to tell an operator to read the name off the author's
    pyproject, and got the field wrong — a certificate for a name that did not
    exist, refused by the author's next build.

    The trap is gone rather than documented around: identity comes from the
    certificate, so the name is one we pick. The runbook has to say that, or
    someone will still go looking for the author's."""
    doc = (_REPO / "docs" / "deployment.md").read_text("utf-8")
    section = doc[doc.index("### 15.6") : doc.index("### 15.7")]

    assert "這是**你**取的" in section
    assert "和作者的 command 叫什麼無關" in section


def test_the_grant_runbook_covers_the_two_ways_a_source_is_written_wrong() -> None:
    """Both are refused by the command, but an operator who reads why first
    does not have to be refused at all."""
    doc = (_REPO / "docs" / "deployment.md").read_text("utf-8")
    section = doc[doc.index("### 15.6") : doc.index("### 15.7")]

    assert "回滾" in section  # too narrow: pinning breaks
    assert "只給網域" in section  # too broad


def test_the_grant_runbook_is_walkable_alone() -> None:
    """It is followed by one person, in production, with nobody to ask. Every
    step that would otherwise be tribal knowledge has to be on the page: where
    the commands run, that a new key needs a release before it does anything,
    what to tell the author, and how to confirm it worked."""
    doc = (_REPO / "docs" / "deployment.md").read_text("utf-8")
    section = doc[doc.index("### 15.6") : doc.index("### 15.7")]

    assert "uv run python -m workspace_app.tooling.grant keygen" in section
    assert "發版之後才生效" in section
    assert "tool-certificate.token" in section  # what the author is told to do
    assert "tool-registry.csv" in section  # where the name is recorded
    assert "size granted by" in section  # how they confirm it landed


def test_the_images_that_fetch_can_be_told_where_the_credential_may_go() -> None:
    """The builder never fetches an artifact, so it has no credential to
    misplace. The other two do, and both need the same knob — a runner that
    can send a token anywhere turns "install this tool" into a way to collect
    one, and it presents to the person as a failed install."""
    for rel in (("sandbox-host", "Dockerfile"), ("sandbox-host", "mcp-runner.Dockerfile")):
        text = _REPO.joinpath(*rel).read_text("utf-8")
        assert "ARG ARTIFACT_HOSTS" in text, rel
        assert "TOOL_ARTIFACT_HOSTS=${ARTIFACT_HOSTS}" in text, rel


def test_the_deployment_docs_separate_setup_from_per_tool_work() -> None:
    """Reading the runbook, it was easy to take the one-time setup — keys, the
    credential's allowed hosts, the images — as something to repeat for every
    tool. It is five things done once and two done per tool, and a doc that
    does not say so turns onboarding a tool into an afternoon."""
    doc = (_REPO / "docs" / "deployment.md").read_text("utf-8")
    section = doc[doc.index("### 15.1") : doc.index("### 15.2")]

    assert "不是**每支工具" in section
    # The three that were previously undocumented or buried elsewhere.
    assert "TOOL_ARTIFACT_HOSTS" in section
    assert "grant keygen" in section
    assert "mcp-runner" in section
    # And what IS per tool, so the reader can tell them apart.
    assert "每支工具要做的" in section
