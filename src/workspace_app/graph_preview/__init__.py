"""``python -m workspace_app.graph_preview`` — see what the graph would be (#697).

Reads a collection, runs the computation production runs, writes JSON locally,
and changes nothing. The graph itself lives in ``kb.graph.preview``; ``__main__``
is settings-driven composition and CLI glue, like ``workspace_app.worker``.

What lives HERE is the one decision the glue has to make before it runs
anything, so that it can be tested.
"""

from __future__ import annotations

from pathlib import Path

from ..config.schema import Settings


def unusable_config(settings: Settings, config_path: Path | None) -> str | None:
    """Why this run cannot work, or ``None``.

    `load()` resolves `./config.yaml` against the CURRENT DIRECTORY and falls
    back to bundled defaults when it finds nothing — and those defaults
    configure no store, so specstar uses its in-memory one, which is empty by
    construction. Every collection id then comes back "not found", and the error
    names the ID, so the first thing anyone checks is the one thing that was
    right. Worse, it is the kind of mistake that looks like the tool working:
    run it from a sibling directory and the answer changes with no sign why.

    So the run is refused up front, and the message says which file was read —
    "none" and "this one, and it configures nothing" are different mistakes that
    send you to different places.
    """
    fs = settings.filestore
    if fs.disk_root or fs.pg_dsn:
        return None
    where = (
        f"the config that was read ({config_path}) configures no store"
        if config_path is not None
        else "no config.yaml was found (looked for --config, then $WORKSPACE_APP_CONFIG, "
        "then ./config.yaml in the current directory)"
    )
    return (
        f"{where}, so this would read an EMPTY in-memory store and report a corpus "
        "with no documents in it. Point it at the deployment's config with "
        "--config /path/to/config.yaml, or run from the directory that holds it."
    )
