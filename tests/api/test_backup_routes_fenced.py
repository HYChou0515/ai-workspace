"""specstar registers `GET /_backup/export` and `POST /_backup/import` globally and
unconditionally (`spec.apply` → `_apply_backup_routes`), and neither consults a
permission checker, an access scope, or a `Depends`. Export hands back every
registered model — blob bytes included — and import defaults to
`on_duplicate=overwrite`, i.e. it replaces the database from an uploaded file.

There is no auth layer in front of them: the `/api` router carries no
`dependencies=`, and an in-cluster caller reaches the service directly (the
`cronjob-*` manifests do exactly that, unauthenticated, by design). Since the
sandbox runs user code, "in-cluster" includes anyone with a workspace.

These tests pin the fence this app puts in front of both doors. See
`docs/plan-backup.md` §6.1 for the reproduction against the unfenced build, and
HYChou0515/specstar#450 (S4) for the upstream report.
"""

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

SECRET = "TOP-SECRET-COLLECTION-NAME-9f3a1c"


def _client(holder: dict[str, str], *, superusers=frozenset()):
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
    )
    return TestClient(app)


def test_export_refuses_a_non_superuser_and_leaks_nothing():
    holder = {"id": "nobody"}
    client = _client(holder, superusers=frozenset({"root"}))
    client.post("/kb/collections", json={"name": SECRET})

    r = client.get("/_backup/export")

    assert r.status_code == 403
    # The status code alone would still pass if the body were streamed anyway,
    # so assert the payload never crosses the wire.
    assert SECRET.encode() not in r.content


def test_import_refuses_a_non_superuser():
    """The destructive half. specstar's import defaults to
    `on_duplicate=overwrite`, so reaching it at all is reaching a
    replace-the-database button."""
    client = _client({"id": "nobody"}, superusers=frozenset({"root"}))

    r = client.post("/_backup/import", files={"file": ("x.acbak", b"anything")})

    assert r.status_code == 403


def test_both_doors_stay_closed_for_a_superuser_too():
    """Deliberate, and the reason is size rather than trust: specstar's export
    buffers the whole archive into a `BytesIO` before it answers (#450 S5), so at
    this deployment's 100 GB – 2 TB the HTTP door cannot serve anyone. Privilege
    does not change that, so the door is closed rather than gated — and the
    refusal names the in-process path that does work."""
    client = _client({"id": "root"}, superusers=frozenset({"root"}))
    client.post("/kb/collections", json={"name": SECRET})

    export = client.get("/_backup/export")
    imported = client.post("/_backup/import", files={"file": ("x.acbak", b"anything")})

    assert export.status_code == 403
    assert SECRET.encode() not in export.content
    assert imported.status_code == 403
    # The refusal has to point somewhere, or it just looks like a bug.
    assert "workspace_app.backup" in export.json()["detail"]
    assert "workspace_app.restore" in imported.json()["detail"]
