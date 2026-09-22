"""The backup must archive every model the API writes, and the only way to hold
that is to build the API's own composition.

`spec.dump` archives what is in the registry. `make_spec` does not register
`WorkspaceFile` — `SpecstarFileStore.__init__` does — so a backup that built its
spec the cheap way would omit an entire model **and still report success**. That
is the exact failure invariant one exists to prevent, and it is invisible until a
restore.

The API's registry is the oracle here, not a list written down beside it: a list
would need updating every time a model is added, which is the drift this is
guarding against.
"""

from __future__ import annotations

import pytest

from workspace_app.__main__ import build_app
from workspace_app.backup.__main__ import build_backup_spec
from workspace_app.config.schema import (
    EmbedderSettings,
    FilestoreSettings,
    KbSettings,
    SandboxSettings,
    Settings,
)


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    # Tool packages are prebuilt bundles the API refuses to boot without; the
    # documented opt-out is an empty PACKAGES (same as tests/test_worker.py).
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "data")),
        # the local sandbox mkdirs its root at boot
        sandbox=SandboxSettings(root=str(tmp_path / "sandbox")),
        # keep boot off the network
        kb=KbSettings(embedder=EmbedderSettings(model="")),
    )


def test_the_backup_registry_is_the_api_registry(settings: Settings):
    api_models = set(build_app(settings, config_dir=None).state.spec.resource_managers)
    backup_models = set(build_backup_spec(settings, config_dir=None).resource_managers)

    assert backup_models == api_models


def test_the_registry_actually_contains_the_model_registered_outside_make_spec(
    settings: Settings,
):
    """Positive control. Without it the parity assertion above would also pass on
    two empty registries, and `workspace-file` is precisely the model that a
    `make_spec`-only backup would drop."""
    backup_models = set(build_backup_spec(settings, config_dir=None).resource_managers)

    assert "workspace-file" in backup_models
    assert len(backup_models) > 10
