/**
 * The workspace path as anyone OUTSIDE the app should ever see it — the FE half of
 * the backend's `files.facade.rel_path` (#549).
 *
 * The store's key is absolute-looking (`/brief.md`) and every write endpoint takes
 * it back happily, so it is the right thing to send over the wire. But it is the
 * wrong thing to SHOW: the sandbox runs a real process whose cwd is the workspace
 * and which has no chroot, so `/brief.md` pasted into `exec` — or into a chat
 * message the agent then acts on — resolves against the SYSTEM root and the file is
 * not found. Anything the user reads or copies therefore goes through here, so the
 * one form that works in every surface is the only one the UI ever teaches.
 */
export function relPath(path: string): string {
  return path.replace(/^\/+/, "");
}
