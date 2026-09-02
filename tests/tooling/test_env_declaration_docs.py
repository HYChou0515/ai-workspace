"""The `env.json` example in the docs is one a tool author can paste (#750).

A declaration format is only as good as the example people copy. This reads the
block out of `docs/extending-the-platform.md` and runs it through the SAME
reader a real bundle goes through, so an example that drifted from the parser
fails here rather than in someone's first build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from workspace_app.tooling.registry import _read_env_needs

_DOCS = Path(__file__).resolve().parents[2] / "docs" / "extending-the-platform.md"


def _documented_env_json() -> str:
    """The ```json block under the #750 declaration heading."""
    body = _DOCS.read_text(encoding="utf-8")
    start = body.index("### 說出你需要哪些變數(#750)")
    block = re.search(r"```json\n(.*?)```", body[start:], re.DOTALL)
    assert block, "the #750 section lost its env.json example"
    # Strip the `//` comments the doc uses to annotate the example — they are
    # for the reader, and JSON has none.
    return "\n".join(
        line for line in block.group(1).splitlines() if not line.strip().startswith("//")
    )


def test_the_documented_example_parses_the_way_the_docs_claim(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "env.json").write_text(_documented_env_json(), encoding="utf-8")

    needs = _read_env_needs(pkg)

    assert needs is not None, "the documented example must be readable"
    by_name = {n.name: n for n in needs}
    assert set(by_name) == {"MY_API_TOKEN", "MY_API_BASE"}
    # The doc says `name` is the only required key and that omitting `required`
    # means "unstated" — both of which the example demonstrates.
    assert by_name["MY_API_TOKEN"].required is True
    assert by_name["MY_API_BASE"].required is None
    assert by_name["MY_API_BASE"].description  # it carries an explanation


def test_the_comment_stripping_cannot_reach_inside_a_value():
    """The stripper drops lines that START with `//`, which JSON makes safe.

    A JSON string cannot contain a raw newline, so no line can begin inside a
    value — which is why a `//` that IS inside one (the example's
    `https://internal/tokens`) survives untouched. Asserted rather than assumed,
    because the reader here is test-only scaffolding and a sloppier one that
    stripped `//` anywhere would quietly test a different example than the docs
    show."""
    raw = _documented_env_json()
    parsed = json.loads(raw)
    assert isinstance(parsed, list) and parsed
    # The URL in the description made it through the stripper intact.
    assert any("https://" in (e.get("description") or "") for e in parsed)
    # And the doc block really does carry comments, so the stripper is load
    # bearing rather than a no-op that would hide a future regression.
    block_start = _DOCS.read_text(encoding="utf-8").index("### 說出你需要哪些變數(#750)")
    original = re.search(
        r"```json\n(.*?)```", _DOCS.read_text(encoding="utf-8")[block_start:], re.DOTALL
    )
    assert original and any(
        line.strip().startswith("//") for line in original.group(1).splitlines()
    )


def test_the_documented_provider_example_is_a_real_implementation():
    """The `IEnvProvider` example in the docs actually satisfies the interface.

    A deploy author copies this block first. If it were missing a member, or
    named one wrongly, the failure would land on THEM at startup — after they
    had written the login logic around a shape that never fit. Compiling the
    documented source and instantiating it moves that failure here.

    The awaited call is replaced (nobody logs into SAP in a unit test), but the
    class, its members, and their names are the doc's own text."""
    body = _DOCS.read_text(encoding="utf-8")
    start = body.index("### 用帳號密碼換出變數(#750,第二方)")
    block = re.search(r"```python\n(.*?)```", body[start:], re.DOTALL)
    assert block, "the #750 provider section lost its python example"

    from workspace_app.api.env_provider import IEnvProvider

    source = block.group(1).replace(
        'await my_sap_client.login(values["user"], values["password"])',
        '"tok-" + values["user"]',
    )
    ns: dict = {}
    exec(compile(source, "<docs>", "exec"), ns)  # noqa: S102 — the doc IS the input

    cls = ns["SapLogin"]
    assert issubclass(cls, IEnvProvider), "the example must implement the seam"
    # Instantiable with no arguments — the factory constructs it that way.
    provider = cls()
    assert provider.id and provider.label
    assert "SAP_TOKEN" in provider.produces
    # It collects a masked field, which is what the prose promises.
    assert any(f.secret for f in provider.inputs)
