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


def test_every_model_transfer_door_is_closed_not_just_the_global_pair():
    """It is a class, not two routes.

    specstar emits `GET /{model}/export` and `POST /{model}/import` for every
    registered model, with exactly the same absence of authorization as the
    global pair. On this deployment that is around ninety doors. Fencing two of
    them and writing "the hole is closed" in the runbook would have left the
    same hole open forty-four times over — 44 models, 88 doors.

    Derived from the registry rather than listed, so a model added tomorrow has
    to be fenced the day it appears — which is the whole point of not
    enumerating.
    """
    holder = {"id": "nobody"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    client = TestClient(app)
    models = sorted(spec.resource_managers)
    assert len(models) > 20, "the registry looks empty — this guard would prove nothing"

    answers = [
        (name, verb, response.status_code)
        for name in models
        for verb, response in (
            ("GET", client.get(f"/{name}/export")),
            ("POST", client.post(f"/{name}/import", files={"file": ("x", b"y")})),
        )
    ]

    # 404 is as closed as 403: a model registered AFTER `spec.apply` — the
    # internal coordination rows — never gets CRUD routes in the first place, so
    # there is no door to fence. What must never appear is a 2xx.
    open_doors = [a for a in answers if a[2] not in (403, 404)]
    assert not open_doors, f"unfenced transfer doors: {open_doors[:5]}"

    # The positive control, because "everything 404s" would satisfy the line
    # above while proving the fence does nothing.
    fenced = [a for a in answers if a[2] == 403]
    assert len(fenced) > 40, f"only {len(fenced)} door(s) were actively fenced"


def test_a_model_export_leaks_nothing():
    """The status code is the mechanism; this is the consequence. Before the
    fence, an unauthenticated `GET /api/collection/export` returned 200 with
    another user's collection name in the body."""
    holder = {"id": "owner"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    client = TestClient(app)
    client.post("/kb/collections", json={"name": SECRET})

    holder["id"] = "nobody"
    response = client.get("/collection/export")

    assert response.status_code == 403
    assert SECRET.encode() not in response.content
