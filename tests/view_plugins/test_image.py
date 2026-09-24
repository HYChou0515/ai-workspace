"""The app image carries the runtime view plugins (#847/#848 PR1 P10).

`csv-table` left the SPA bundle, so an image built without the plugin stage
would silently lose it: every `view: csv-table` file would say "Unsupported
view kind". These read the Dockerfile's text, like `tests/chat_video/test_image.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from workspace_app.view_plugins.discovery import DEFAULT_PLUGINS_DIR

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "docker" / "Dockerfile").read_text()


def _stages() -> list[str]:
    return re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)\s*$", DOCKERFILE, re.MULTILINE)


def test_a_view_plugins_stage_builds_before_the_app_and_api():
    stages = _stages()
    assert "view-plugins" in stages
    assert stages.index("view-plugins") < stages.index("app") < stages.index("api")


def test_it_builds_every_plugin_through_the_one_shared_script():
    stage = DOCKERFILE.split("AS view-plugins", 1)[1].split("\nFROM ", 1)[0]
    assert "view-plugins/build-web.mjs" in stage


def test_the_app_stage_installs_them_where_the_default_dir_resolves():
    # In the image the repo root is /app (see the sample-skills note), so the
    # default `<repo>/.view-plugins` is /app/.view-plugins.
    assert DEFAULT_PLUGINS_DIR.name == ".view-plugins"
    app = DOCKERFILE.split("AS app", 1)[1].split("\nFROM ", 1)[0]
    assert re.search(r"^COPY --from=view-plugins /out \./\.view-plugins$", app, re.MULTILINE)


def test_no_node_modules_rides_into_the_build_context():
    """A plugin's own `web/node_modules` (left by `make view-plugins`) must not
    be copied into the image's plugin stage: a host-built esbuild is the wrong
    platform there. `.dockerignore`'s bare `node_modules` matches the context
    root only."""
    ignore = (REPO / ".dockerignore").read_text().split()
    assert "**/node_modules" in ignore


def test_the_image_checks_the_plugins_it_ships():
    app = DOCKERFILE.split("AS app", 1)[1].split("\nFROM ", 1)[0]
    copied = app.index("COPY --from=view-plugins")
    assert app.index("RUN python -m workspace_app.view_plugin check") > copied
