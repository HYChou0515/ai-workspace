"""Rendering the item's `env_vars` into the file the tool launchers read, and
getting it into the sandbox once per turn.

The format is one `KEY=VALUE` line, parsed by our own launcher loop — never
`source`d. A shell `source` would make import/export symmetric for free, but it
also rewrites any value containing `$`, a backtick or `$(…)`: an API key that
arrives subtly wrong, with no signal anywhere. So the file stays dumb and the
reader stays ours.
"""

import pytest

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.user_env import render_user_env
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec


class TestRender:
    def test_one_line_per_variable(self):
        assert render_user_env({"A": "1", "B": "2"}) == "A=1\nB=2\n"

    def test_nothing_at_all_renders_empty(self):
        assert render_user_env({}) == ""

    def test_values_are_written_verbatim(self):
        # The launcher `export`s the line as one word rather than sourcing it,
        # so none of this needs escaping — and MUST NOT be escaped, or the tool
        # receives a different key than the user typed.
        value = "a=b c#d$e`f'g\"h"
        assert render_user_env({"TOKEN": value}) == f"TOKEN={value}\n"

    def test_insertion_order_is_kept(self):
        assert render_user_env({"Z": "1", "A": "2"}) == "Z=1\nA=2\n"

    @pytest.mark.parametrize("bad", ["A\nB", "A\rB"])
    def test_a_newline_in_a_name_cannot_smuggle_a_second_variable(self, bad):
        # The format is line-oriented, so a newline in a NAME would inject an
        # extra assignment the user never made. A text input cannot produce one,
        # but the API is reachable without the UI.
        assert render_user_env({bad: "1", "OK": "2"}) == "OK=2\n"

    @pytest.mark.parametrize("bad", ["1\nEVIL=2", "1\rEVIL=2"])
    def test_a_newline_in_a_value_cannot_smuggle_a_second_variable(self, bad):
        assert render_user_env({"A": bad, "OK": "2"}) == "OK=2\n"

    def test_a_nameless_variable_is_dropped(self):
        assert render_user_env({"": "1", "OK": "2"}) == "OK=2\n"

    def test_a_name_carrying_the_separator_is_dropped(self):
        # `A=B=1` would read back as the name `A` — a variable the user cannot
        # see they created.
        assert render_user_env({"A=B": "1", "OK": "2"}) == "OK=2\n"


class TestDelivery:
    async def test_ensure_sandbox_delivers_the_variables(self):
        sandbox = MockSandbox()
        ctx = AgentToolContext(sandbox=sandbox, user_env={"API_KEY": "sk-1"})

        handle = await ctx.ensure_sandbox()

        assert sandbox.user_env(handle) == "API_KEY=sk-1\n"

    async def test_an_item_with_no_variables_still_writes_the_file(self):
        # Emptiness has to REACH the sandbox: the previous turn may have left a
        # file behind, and the user deleting their last variable must not leave
        # the tools reading a stale one.
        sandbox = MockSandbox()
        ctx = AgentToolContext(sandbox=sandbox, user_env={})

        handle = await ctx.ensure_sandbox()

        assert sandbox.user_env(handle) == ""

    async def test_the_next_turn_rewrites_it_on_the_SAME_sandbox(self):
        # A turn gets a fresh context but the same warm sandbox, so the file is
        # rebuilt from the item every time. That is how an edit between turns
        # lands at all, and the only way a DELETED variable actually goes.
        sandbox = MockSandbox()
        handle = await sandbox.create(SandboxSpec())

        async def warm(_progress):
            return handle

        first = AgentToolContext(
            sandbox=sandbox, user_env={"A": "1", "B": "2"}, ensure_sandbox_via=warm
        )
        await first.ensure_sandbox()
        assert sandbox.user_env(handle) == "A=1\nB=2\n"

        second = AgentToolContext(sandbox=sandbox, user_env={"A": "1"}, ensure_sandbox_via=warm)
        await second.ensure_sandbox()

        assert sandbox.user_env(handle) == "A=1\n"


class TestWiring:
    """The delivery is only real if a TURN actually carries the item's values.
    Testing the context alone passes whether or not anything ever populates it —
    which is exactly how the `ask_user` card shipped dead (#591)."""

    @staticmethod
    def _app_and_locator():
        from workspace_app.api import ScriptedAgentRunner, create_app
        from workspace_app.api.locator import ItemLocator
        from workspace_app.apps.catalog import AppCatalog
        from workspace_app.config.schema import Settings
        from workspace_app.filestore.memory import MemoryFileStore
        from workspace_app.resources import make_spec

        spec = make_spec(default_user="u")
        app = create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=ScriptedAgentRunner([]),
        )
        locator = ItemLocator(spec, AppCatalog(presets=Settings().agents.presets))
        return app, locator

    async def test_a_turn_carries_the_items_env_vars(self):
        from ..api._client import TestClient

        app, locator = self._app_and_locator()
        client = TestClient(app)
        rid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]
        client.patch(
            f"/rca-investigation/{rid}",
            json=[{"op": "replace", "path": "/env_vars", "value": {"API_KEY": "sk-1"}}],
        )

        assert locator.env_vars_of(rid) == {"API_KEY": "sk-1"}

    async def test_an_unknown_item_has_none(self):
        _app, locator = self._app_and_locator()
        assert locator.env_vars_of("nope") == {}
