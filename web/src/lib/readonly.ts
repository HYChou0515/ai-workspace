/**
 * #205 — the read-only file convention, mirrored from the backend
 * (`api/app.py::_is_readonly_path`). A file is read-only when any path segment is
 * the reserved `.readonly` directory (like the `/.workflow/` journal folder, #136):
 * the workflow writes the diff "before" snapshot (`.readonly/context-card.current.md`)
 * there so the IDE renders it non-editable and the buffer never tries to save it
 * (the server also refuses the PUT). A computed convention — no per-file metadata.
 */
export function isReadOnlyPath(path: string): boolean {
  return path.split("/").includes(".readonly");
}
