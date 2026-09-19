import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * plan-skill-hub-ui-polish D1 — the panel pads its content by default, and
 * this is what keeps the default from landing on a modal that lays out its
 * own interior.
 *
 * Review round 1 of #826 found the class had been enumerated wrong: "callers
 * that pass no `panelStyle`" (6) was taken for "callers that pass no
 * padding" (9), and three modals with their own header / body / footer
 * (AppNewItem, ⌘P, the environment editor) quietly gained a 20 px frame. So
 * every `<ModalShell` site now makes its padding decision where a test can
 * read it: a `padding` key in `panelStyle` (inline, or in the object the
 * prop names), or an entry in TAKES_DEFAULT saying "the shell's padding is
 * what this one wants". A new modal has to pick — that is the point.
 */

const SRC = join(new URL(".", import.meta.url).pathname, "..");

/** Sites that want the shell's own padding. Listed, not inferred. */
const TAKES_DEFAULT: Record<string, number> = {
  "pages/SkillHubEntryPage.tsx": 2, // the transfer and the new-item dialogs
};

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) tsxFiles(full, out);
    else if (name.endsWith(".tsx") && !name.includes(".test.")) out.push(full);
  }
  return out;
}

/** The text of every `<ModalShell …>` opening tag in `src`, brace- and
 * string-aware so a `=>` or a `>` inside an attribute does not end it early. */
function openingTags(src: string): string[] {
  const out: string[] = [];
  let at = src.indexOf("<ModalShell");
  while (at !== -1) {
    let i = at + "<ModalShell".length;
    let depth = 0;
    let quote: string | null = null;
    for (; i < src.length; i++) {
      const ch = src[i];
      if (quote) {
        if (ch === "\\") i++;
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'" || ch === "`") quote = ch;
      else if (ch === "{") depth++;
      else if (ch === "}") depth--;
      else if (ch === ">" && depth === 0) break;
    }
    out.push(src.slice(at, i + 1));
    at = src.indexOf("<ModalShell", i + 1);
  }
  return out;
}

/** Whether a tag decides its padding: `panelStyle={{ …padding… }}`, or
 * `panelStyle={name}` where `name`'s object literal in the same file has a
 * `padding` key. */
function decidesPadding(tag: string, src: string): boolean {
  const at = tag.indexOf("panelStyle={");
  if (at === -1) return false;
  // Brace-matched, not `\{([^]*)\}`: a greedy match ran on to the last brace
  // of the tag and read a `backdropStyle` padding as the panel's (the first
  // mutation probe passed for exactly that reason).
  let i = at + "panelStyle={".length;
  let depth = 1;
  const start = i;
  for (; i < tag.length && depth > 0; i++) {
    if (tag[i] === "{") depth++;
    else if (tag[i] === "}") depth--;
  }
  const value = tag.slice(start, i - 1).trim();
  if (/\bpadding\s*:/.test(value)) return true;
  const ident = /^[A-Za-z_$][\w$]*$/.exec(value)?.[0];
  if (!ident) return false;
  const def = new RegExp(`const ${ident}\\b[^=]*=\\s*\\{([^]*?)\\n\\};`).exec(src);
  return def !== null && /\bpadding\s*:/.test(def[1]);
}

describe("every <ModalShell> site decides its padding", () => {
  const files = tsxFiles(SRC).filter((f) => !f.endsWith("components/ModalShell.tsx"));
  const sites = files.flatMap((f) => {
    const src = readFileSync(f, "utf8");
    const rel = relative(SRC, f);
    return openingTags(src).map((tag) => ({ rel, explicit: decidesPadding(tag, src) }));
  });

  it("finds the sites it is guarding", () => {
    expect(sites.length).toBeGreaterThanOrEqual(30);
  });

  it("every site passes a padding, or is listed as taking the shell's default", () => {
    const undecided = sites.filter((s) => !s.explicit && !(s.rel in TAKES_DEFAULT)).map((s) => s.rel);
    expect(undecided).toEqual([]);
  });

  it("the default-takers list matches the sites that really take it — no stale entries", () => {
    const takers: Record<string, number> = {};
    for (const s of sites) if (!s.explicit) takers[s.rel] = (takers[s.rel] ?? 0) + 1;
    expect(takers).toEqual(TAKES_DEFAULT);
  });
});
