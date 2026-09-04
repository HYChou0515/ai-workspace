/**
 * The WUI's folder, and the one rule that keeps a page inside it.
 *
 * A WUI is identified by its view file, and everything it may write lives
 * beside that file. So "which folder" and "is this path inside it" are the same
 * question asked twice, and both are answered here rather than at each call
 * site — a scope check that exists in three places is a scope check that is
 * about to exist in two.
 */

/** The folder holding a `*.ai.yaml` view file (`""` at the workspace root). */
export function wuiFolder(viewPath: string): string {
  const cut = viewPath.lastIndexOf("/");
  return cut <= 0 ? "" : viewPath.slice(0, cut);
}

/**
 * Resolve a folder-relative reference to an absolute workspace path, or `null`
 * when it is not one.
 *
 * Refused: anything absolute (it was never folder-relative), and anything whose
 * `..` segments climb past the folder. The climb is checked by COUNTING segments
 * during the walk, not by comparing the result to the folder afterwards — a
 * string comparison says `/sales2` starts with `/sales` and lets a sibling
 * folder through.
 */
export function resolveInFolder(folder: string, ref: string): string | null {
  if (!ref || ref.startsWith("/")) return null;

  const out: string[] = [];
  for (const seg of ref.split("/")) {
    if (seg === "" || seg === ".") continue; // `a//b` and `./a` are just `a`
    if (seg !== "..") {
      out.push(seg);
      continue;
    }
    // Only segments this reference itself contributed may be popped; running
    // out means the next `..` would leave the folder.
    if (out.length === 0) return null;
    out.pop();
  }
  if (out.length === 0) return null; // `.` / `a/..` name the folder, not a file
  return `${folder}/${out.join("/")}`;
}

/**
 * A file an assembled page references, resolved against the ENTRY's directory
 * and bounded by the WUI's folder.
 *
 * Those are two different things as soon as a page is BUILT: `dist/index.html`
 * saying `./assets/x.js` means the file next to itself. Resolving from the WUI
 * folder instead put every reference one directory too high and nothing was
 * inlined — the page rendered blank, which is the same lost-origin mistake that
 * broke markdown images (#717).
 *
 * The boundary stays the folder, not the entry's directory, so a built page can
 * still reach a sibling of its view file (`../logo.png`) without being able to
 * reach the item.
 */
export function resolveAssetPath(folder: string, entryDir: string, ref: string): string | null {
  if (!ref || ref.startsWith("/")) return null;
  const out = entryDir.split("/").filter(Boolean);
  const floor = folder.split("/").filter(Boolean).length;
  for (const seg of ref.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg !== "..") {
      out.push(seg);
      continue;
    }
    // Counted against the FOLDER's depth, not the entry's: climbing back to the
    // view file is ordinary; climbing past it is the escape this refuses.
    if (out.length <= floor) return null;
    out.pop();
  }
  return out.length > floor ? `/${out.join("/")}` : null;
}

/** Normalise a workspace-absolute path, or `null` if it climbs past the root or
 * names the root itself. Same segment walk as above, so `/sales/../notes.md`
 * cannot arrive somewhere a string comparison would later mistake for inside. */
function normalizeAbsolute(path: string): string | null {
  const out: string[] = [];
  for (const seg of path.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg !== "..") {
      out.push(seg);
      continue;
    }
    if (out.length === 0) return null;
    out.pop();
  }
  return out.length === 0 ? null : `/${out.join("/")}`;
}

/**
 * A path the page may READ: anywhere in the item.
 *
 * A leading `/` means the workspace root; anything else means next to the page,
 * which is what an author writing `readFile("data.json")` means. The same two
 * spellings the platform already resolves for a markdown ref.
 */
export function resolveReadPath(folder: string, path: string): string | null {
  return path.startsWith("/") ? normalizeAbsolute(path) : resolveInFolder(folder, path);
}

/**
 * Is this read asking for the page's OWN file — the one place absence is
 * ordinary rather than a mistake?
 *
 * Deliberately NOT `resolveWritePath(...) !== null`. That function answers a
 * WRITE question and opens with `if (folder === "") return null` — a security
 * decision about a root-level page, which may not write anywhere. Borrowing it
 * to answer a READ question imported that decision wholesale: a root page,
 * which the reference documents as able to read, had EVERY missing read
 * reported, including the bare read of its own data file on its very first
 * open. An alarm that always fires is the failure this whole distinction exists
 * to prevent.
 *
 * A root page has no folder, so "its own" is what it asked for relatively.
 */
export function isOwnFile(folder: string, raw: string): boolean {
  if (folder === "") return !raw.startsWith("/");
  const abs = raw.startsWith("/") ? normalizeAbsolute(raw) : resolveInFolder(folder, raw);
  return abs !== null && abs.startsWith(`${folder}/`);
}

/**
 * A path the page may WRITE or DELETE: only inside its own folder.
 *
 * Both spellings are normalised FIRST and the containment is then checked on
 * segments, because `/sales2` and `/sales.bak` both pass a `startsWith("/sales")`
 * test and neither is inside `/sales`.
 */
export function resolveWritePath(folder: string, path: string): string | null {
  // A view file at the workspace ROOT has no folder of its own, and the honest
  // reading of "only its own folder" is then NOTHING — not everything. Treating
  // it as the whole workspace removed the containment this module exists for,
  // silently and with no signal in the pane: such a page could overwrite the
  // item's notes, another WUI's entry, and the skills and workflow scripts the
  // platform later runs on the user's behalf.
  if (folder === "") return null;
  const abs = path.startsWith("/") ? normalizeAbsolute(path) : resolveInFolder(folder, path);
  if (abs === null) return null;
  return abs.startsWith(`${folder}/`) ? abs : null;
}
