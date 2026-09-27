"""The view SDK major is said in one place — `web/src/viewPlugins/sdkVersion.ts`,
what the browser loader checks a plugin against — and everything that writes a
plugin's `"sdk"` must say the same (#861: the marking shape changed, so the major
moved to 2; a scaffold or a first-party plugin left on 1 is refused by the
loader, per panel).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from workspace_app.view_plugin import scaffold

ROOT = Path(__file__).resolve().parents[2]


def _host_major() -> str:
    text = (ROOT / "web/src/viewPlugins/sdkVersion.ts").read_text()
    m = re.search(r'export const SDK_VERSION = "(\d+)', text)
    assert m, "sdkVersion.ts no longer declares SDK_VERSION as a string literal"
    return m.group(1)


def test_a_scaffolded_plugin_declares_the_hosts_major(tmp_path):
    scaffold.scaffold_plugin(tmp_path, "demo")
    manifest = json.loads((tmp_path / "demo" / "plugin.json").read_text())
    assert manifest["sdk"] == _host_major()


def test_the_first_party_plugins_declare_the_hosts_major():
    for name in ("chart", "csv-table"):
        manifest = json.loads((ROOT / "view-plugins" / name / "plugin.json").read_text())
        assert manifest["sdk"] == _host_major(), name
