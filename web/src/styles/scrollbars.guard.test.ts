// A scroll container that forgets `.scrollable` gets the browser's default bar:
// wider, differently coloured, and on macOS an overlay that hides until you
// move. Thirty of this app's thirty-nine scroll containers had drifted that way
// before anyone noticed, because nothing said so — the rule lived only in the
// components that happened to remember it.
//
// So the rule is a test rather than a habit. It scans the source, which is
// blunt, and that bluntness is the point: it fails on the FORTY-FIRST container
// too, written by someone who never read this file.
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..");

/**
 * Files allowed to scroll with the browser's default bar, and why. Adding a
 * line here is a decision; forgetting the class is not.
 */
const UNTHEMED: Record<string, string> = {
  // Content viewers, not panels. They scroll BOTH axes, and `scrollbar-width:
  // thin` makes a horizontal bar a harder target on a wide table — Baymard
  // measures oversensitive scrollbars as their own usability problem. The
  // default bar is the better tool here, so this is a choice, not a gap.
  "renderers/DataGrid.tsx": "wide data grid, horizontal scroll",
  "renderers/JsonlView.tsx": "wide record view, horizontal scroll",
  "renderers/rawFallback.tsx": "raw file bytes, horizontal scroll",
  "renderers/entity/RecordFileRenderer.tsx": "record table, horizontal scroll",
  "pages/SanityTable.tsx": "wide results table, horizontal scroll",
  // Second-party content in a sandbox. Painting our scrollbar onto somebody
  // else's page is not ours to do, and it would fight whatever they set.
  "renderers/wui/WuiView.tsx": "second-party sandboxed content",
  // The overflow lives in a shared style OBJECT here; the class is on the four
  // JSX usage sites instead, which this scanner cannot see from the object.
  "components/ItemShareDialog.tsx": "themed at the usage sites (shared style object)",
  "components/PermissionDialog.tsx": "themed at the usage sites (shared style object)",
};

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walk(full);
    if (!/\.tsx$/.test(e.name) || e.name.includes(".test.")) return [];
    return [full];
  });
}

describe("every scroll container wears the app's scrollbar", () => {
  it("or is on the list of deliberate exceptions", () => {
    const offenders: string[] = [];

    for (const file of walk(SRC)) {
      const rel = path.relative(SRC, file).split(path.sep).join("/");
      if (rel in UNTHEMED) continue;
      const src = fs.readFileSync(file, "utf8");
      for (const m of src.matchAll(/overflowY:\s*"auto"|overflow:\s*"auto"/g)) {
        const at = m.index ?? 0;
        const tagStart = src.lastIndexOf("<", at);
        // The class may sit before or after the style prop in a multi-line tag,
        // so look at the whole neighbourhood rather than just what precedes it.
        const near = src.slice(Math.max(0, tagStart), at + 400);
        if (near.includes("scrollable")) continue;
        offenders.push(`${rel}:${src.slice(0, at).split("\n").length}`);
      }
    }

    expect(offenders).toEqual([]);
  });

  it("keeps the exception list honest", () => {
    // An exception for a file that no longer scrolls is a stale licence: it
    // would let the NEXT scroll container in that file through unnoticed.
    const stale = Object.keys(UNTHEMED).filter((rel) => {
      const full = path.join(SRC, rel);
      if (!fs.existsSync(full)) return true;
      return !/overflowY:\s*"auto"|overflow:\s*"auto"/.test(fs.readFileSync(full, "utf8"));
    });

    expect(stale).toEqual([]);
  });
});
