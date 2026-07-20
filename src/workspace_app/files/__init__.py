"""Workspace file access — a single facade the agent tools and the API file
routes go through, so there's one chokepoint for *where* files actually live.

P1: it delegates straight to `FileStore` (behaviour identical to before).
P2 will flip its internals to route by sandbox liveness — reads/writes go to
the live sandbox when one is up (the single source of truth) and to the
FileStore snapshot when it's cold — without any caller changing.
"""

from .facade import WorkspaceFiles, WorkspaceFull, rel_path

__all__ = ["WorkspaceFiles", "WorkspaceFull", "rel_path"]
