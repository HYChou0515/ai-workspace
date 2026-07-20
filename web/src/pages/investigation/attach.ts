/**
 * Chat-composer file attach (#198). The composer's 📎 stages file(s) into the
 * item's profile-configured `upload_dir` (default `uploads/`) — the same folder a
 * Topic Hub workflow globs — then drops the path(s) into the draft so the user can
 * send or run a workflow. Folders + big + binary files are allowed; the backend's
 * streaming `PUT files` cap (413) is the only size gate. This module holds the pure
 * logic (path derivation, draft text, upload orchestration with progress) so the UI
 * in AgentPanel stays thin and the behaviour is unit-testable.
 */
import { mapWithConcurrency } from "../../api/concurrency";

/** The folder a chat attach stages into for the item's active profile (#198), from
 * the App manifest's profile list. Unknown / missing profile → `uploads` (the default). */
export function resolveUploadDir(
  profiles: { name: string; upload_dir: string }[],
  profileName: string,
): string {
  return profiles.find((p) => p.name === profileName)?.upload_dir ?? "uploads";
}

/** The workspace path a chat-attached file lands at: `{upload_dir}/{rel}`, preserving
 * a folder pick's relative path (`webkitRelativePath`) so subtrees keep their shape.
 *
 * Workspace-RELATIVE, with no leading slash. This same string is both the PUT key
 * (the backend canonicalises it either way) and the path `attachPrompt` shows the
 * model — and the model's own `list_files` prints `uploads/a.csv`, while its shell
 * reads a leading `/` as the system root. The draft is the first thing it ever
 * learns about an attached file, so it has to teach the form that works. */
export function uploadPathFor(uploadDir: string, file: File): string {
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
  return `${uploadDir}/${rel}`.replace(/\/+/g, "/").replace(/^\//, "");
}

/** The longest shared directory prefix of `paths` (always ends in `/`). */
function commonDir(paths: string[]): string {
  const split = paths.map((p) => p.split("/"));
  const first = split[0];
  let i = 0;
  for (; i < first.length - 1; i++) {
    if (!split.every((s) => s[i] === first[i])) break;
  }
  // The shared segments exclude the differing filename, so the join never ends in "/".
  return first.slice(0, i).join("/") + "/";
}

// >10 attached paths collapse to a one-line summary so a folder drop never explodes
// the draft into hundreds of lines (grill Q3).
const SUMMARY_THRESHOLD = 10;

/** The draft text injected after staging files (grill Q3): a single file → its path;
 * a handful → one path per line; many (>10) → a `{n} files under {dir}` summary. */
export function attachPrompt(paths: string[]): string {
  if (paths.length === 0) return "";
  if (paths.length === 1) return `Attached \`${paths[0]}\`.`;
  if (paths.length <= SUMMARY_THRESHOLD) {
    return "Attached:\n" + paths.map((p) => `- \`${p}\``).join("\n");
  }
  return `Attached ${paths.length} files under \`${commonDir(paths)}\`.`;
}

export interface AttachResult {
  /** Paths that landed in the store, in input order. */
  uploaded: string[];
  /** Paths the server rejected for exceeding the single-file size cap (413). */
  tooLarge: string[];
  /** Paths the server rejected because the workspace is over its total quota
   * (507, #245) — distinct from `tooLarge` so the UI can say "out of space". */
  overQuota: string[];
  /** Paths that failed for any other reason. */
  failed: string[];
}

export interface AttachProgress {
  loadedBytes: number;
  totalBytes: number;
  doneFiles: number;
  totalFiles: number;
}

/** Stage `files` into `uploadDir` through the injected `upload`, a few in flight at a
 * time (a folder pick can be thousands of files). A 413 routes to `tooLarge`, any other
 * error to `failed`; both skip that file and keep going (one bad file never aborts the
 * batch). `onProgress` fires with aggregate byte + file counts for a single progress bar. */
export async function runAttach(opts: {
  files: File[];
  uploadDir: string;
  upload: (path: string, file: File, onChunk?: (loaded: number) => void) => Promise<void>;
  onProgress?: (p: AttachProgress) => void;
  concurrency?: number;
}): Promise<AttachResult> {
  const { files, uploadDir, upload, onProgress, concurrency = 4 } = opts;
  const totalBytes = files.reduce((n, f) => n + f.size, 0);
  const totalFiles = files.length;
  const loaded = new Array<number>(totalFiles).fill(0);
  let doneFiles = 0;
  const report = () =>
    onProgress?.({
      loadedBytes: loaded.reduce((a, b) => a + b, 0),
      totalBytes,
      doneFiles,
      totalFiles,
    });

  const uploaded: (string | null)[] = new Array(totalFiles).fill(null);
  const tooLarge: (string | null)[] = new Array(totalFiles).fill(null);
  const overQuota: (string | null)[] = new Array(totalFiles).fill(null);
  const failed: (string | null)[] = new Array(totalFiles).fill(null);

  await mapWithConcurrency(files, concurrency, async (file, i) => {
    const path = uploadPathFor(uploadDir, file);
    try {
      await upload(path, file, (n) => {
        loaded[i] = n;
        report();
      });
      loaded[i] = file.size;
      uploaded[i] = path;
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 413) tooLarge[i] = path;
      else if (status === 507) overQuota[i] = path;
      else failed[i] = path;
    } finally {
      doneFiles++;
      report();
    }
  });

  const drop = (xs: (string | null)[]): string[] => xs.filter((x): x is string => x !== null);
  return {
    uploaded: drop(uploaded),
    tooLarge: drop(tooLarge),
    overQuota: drop(overQuota),
    failed: drop(failed),
  };
}
