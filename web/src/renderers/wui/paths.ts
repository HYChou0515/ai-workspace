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
