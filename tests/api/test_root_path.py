"""root_path belongs on the FastAPI app (so the OpenAPI/servers + generated
URLs respect a reverse-proxy mount), not just on uvicorn.run."""

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app(**kw):
    spec = make_spec()
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        **kw,
    )


def test_root_path_is_applied_to_the_app() -> None:
    assert _app(root_path="/rca").root_path == "/rca"


def test_root_path_defaults_to_empty() -> None:
    assert _app().root_path == ""
