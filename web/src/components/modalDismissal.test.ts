import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * #779 P6 — the rule for leaving a modal lives in ONE place, and this is what
 * keeps it there.
 *
 * The issue was never a handful of modals behaving badly. It was that "does a
 * click beside the panel close this?" had three different answers plus a
 * Mantine default, because nothing stopped the next person from hand-rolling a
 * fourth overlay. Rules that live only in a doc drift; this one fails a test.
 *
 * Two guards, because a hand-rolled overlay can be spelled two ways:
 *  - a `role="presentation"` wrapper that closes on click (the a11y-correct
 *    spelling), and
 *  - a bare `position: fixed; inset: 0` div with an onClick (the quick one).
 *
 * The allowlist is deliberately short and explicit. A dropdown's click-away
 * catcher looks identical in the source but is not a modal backdrop: it dismisses
 * a menu, there is nothing to lose behind it, and closing on an outside click is
 * the whole point. Adding to this list should feel like a decision.
 */

const SRC = join(new URL(".", import.meta.url).pathname, "..");

/** Owns the shared modal + confirm behaviour — the rule lives here. */
const SHELLS = ["components/ModalShell.tsx", "components/Dialog.tsx"];

/** Click-away catchers for dropdowns/menus, not modal backdrops. A menu holds
 * nothing the user typed, so dismissing it on an outside click is correct. */
const CLICK_AWAY = [
  "components/ModelEffortPicker.tsx",
  "pages/investigation/FileTree.tsx",
  "pages/investigation/WorkspaceShell.tsx",
];

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) tsxFiles(full, out);
    else if (name.endsWith(".tsx") && !name.includes(".test.")) out.push(full);
  }
  return out;
}

const files = tsxFiles(SRC).map((f) => ({
  path: relative(SRC, f).split("\\").join("/"),
  text: readFileSync(f, "utf8"),
}));

describe("#779 — modal dismissal has one owner", () => {
  it("finds source to scan (guards against a broken walker reporting all-clear)", () => {
    expect(files.length).toBeGreaterThan(100);
    expect(files.map((f) => f.path)).toContain("components/ModalShell.tsx");
  });

  it("has no hand-rolled backdrop that closes on click", () => {
    const offenders = files
      .filter((f) => !SHELLS.includes(f.path))
      .filter((f) => /role="presentation"[^>]*onClick|onClick[^>]*role="presentation"/s.test(f.text))
      .map((f) => f.path);

    expect(offenders).toEqual([]);
  });

  it("has no full-screen fixed overlay with an onClick outside the shells", () => {
    // Matched per opening TAG, not per file: `ItemMembersPanel` has a centred
    // `inset: 0` info box and, elsewhere, an onClick — true of the file, false
    // of any single element. A file-wide match would report it forever, and a
    // guard that cries wolf gets an allowlist entry instead of a fix.
    const hasBackdropTag = (text: string) =>
      (text.match(/<[a-zA-Z][^>]*>/g) ?? []).some(
        (tag) =>
          /onClick/.test(tag) && /position:\s*"fixed"/.test(tag) && /inset:\s*0/.test(tag),
      );

    const offenders = files
      .filter((f) => !SHELLS.includes(f.path) && !CLICK_AWAY.includes(f.path))
      .filter((f) => hasBackdropTag(f.text))
      .map((f) => f.path);

    expect(offenders).toEqual([]);
  });

  it("keeps the safe default: ModalShell does not close on backdrop unless asked", () => {
    const shell = files.find((f) => f.path === "components/ModalShell.tsx")!.text;
    expect(shell).toMatch(/closeOnBackdrop\s*=\s*false/);
  });
});
