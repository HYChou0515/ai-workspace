"""An App reads its own files only (final review of docs/plan-model-settings.md):
`prompt_file` is joined to the App's folder, and the settings page can change
it — a path that leaves the folder is refused where the file is read."""

from __future__ import annotations

import pytest

from workspace_app.apps.catalog import _read_app_text


@pytest.mark.parametrize(
    "rel", ["../../../../proc/self/environ", "/etc/passwd", "../rca/prompt.md"]
)
def test_a_path_outside_the_app_folder_is_not_read(rel):
    with pytest.raises(ValueError, match="outside"):
        _read_app_text("playground", rel)


def test_the_apps_own_prompt_is_read():
    from workspace_app.apps.manifest import load_app_manifest

    assert _read_app_text("playground", load_app_manifest("playground").agent.prompt_file)
