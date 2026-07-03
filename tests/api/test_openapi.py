"""The OpenAPI schema is built lazily.

specstar's `spec.openapi(app)` walks every registered route (~1600) and rebuilds
the whole schema — ~3.5s, the single biggest cost inside `create_app`. The running
server needs it only when `/openapi.json` or `/docs` is hit, and the test suite
builds an app per test, so paying it eagerly dominated CI wall time. `create_app`
now defers it behind FastAPI's `app.openapi()` hook: the schema stays unset until
first access, then specstar's customisation runs once and is cached.
"""

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app():
    spec = make_spec()
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )


def test_openapi_is_not_built_eagerly() -> None:
    """create_app must NOT pay the ~3.5s schema build — the schema is unset until
    something asks for it. This laziness is the whole point of the change."""
    app = _app()
    assert app.openapi_schema is None


def test_first_access_runs_specstar_customization_then_caches() -> None:
    """First `app.openapi()` builds the schema WITH specstar's customisation (its
    injected components + all custom routes appear, so FE/Swagger discovery stays
    complete); a second call is served from the cache, not rebuilt."""
    app = _app()

    schema = app.openapi()

    # specstar's customize ran (it injects these components) — not just FastAPI's
    # bare get_openapi. This is what the old eager call at create_app time guarded.
    assert "ResourceMeta" in schema["components"]["schemas"]
    # all the hand-written + CRUD routes are discoverable.
    assert len(schema["paths"]) > 20

    # second access hits the cached branch (identity-stable, no rebuild).
    assert app.openapi() is schema
