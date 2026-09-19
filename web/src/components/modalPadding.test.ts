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

/**
 * `src` with its comments blanked (string-aware): a `//` or `/*` inside a
 * string stays, and everything else a comment says is gone — so a stray
 * apostrophe in a `// shell's` remark cannot open a "string" that swallows
 * the rest of the file, and a `// padding: none` remark cannot pass for a
 * key. Both were round-2 findings on the first version of this guard.
 */
function stripComments(src: string): string {
  let out = "";
  let quote: string | null = null;
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    const next = src[i + 1];
    if (quote) {
      out += ch;
      if (ch === "\\") {
        out += next ?? "";
        i++;
      } else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch;
      out += ch;
    } else if (ch === "/" && next === "/") {
      while (i < src.length && src[i] !== "\n") i++;
      out += "\n";
    } else if (ch === "/" && next === "*") {
      const end = src.indexOf("*/", i + 2);
      i = end === -1 ? src.length : end + 1;
    } else out += ch;
  }
  return out;
}

/** The body between the `{` at `open` and its matching `}` (string-aware). */
function braced(text: string, open: number): string {
  let depth = 0;
  let quote: string | null = null;
  for (let i = open; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) return text.slice(open + 1, i);
  }
  return text.slice(open + 1);
}

/** The text of every `<ModalShell …>` opening tag in comment-free `src`,
 * brace- and string-aware so a `=>` or a `>` inside an attribute does not end
 * it early. `<ModalShellX` is another component and is not matched. */
function openingTags(src: string): string[] {
  const out: string[] = [];
  const re = /<ModalShell(?=[\s/>])/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    let i = m.index + "<ModalShell".length;
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
    out.push(src.slice(m.index, i + 1));
    re.lastIndex = i + 1;
  }
  return out;
}

/** Whether a tag decides its padding: `panelStyle={{ …padding… }}`, or
 * `panelStyle={name}` where `name`'s object literal in the same (comment-free)
 * file has a `padding` key — the literal brace-matched from its `{`, one line
 * or many. */
function decidesPadding(tag: string, src: string): boolean {
  const at = tag.indexOf("panelStyle={");
  if (at === -1) return false;
  const value = braced(tag, at + "panelStyle=".length).trim();
  if (/\bpadding\s*:/.test(value)) return true;
  const ident = /^[A-Za-z_$][\w$]*$/.exec(value)?.[0];
  if (!ident) return false;
  const def = new RegExp(`const ${ident}\\b[^=]*=\\s*\\{`).exec(src);
  if (!def) return false;
  return /\bpadding\s*:/.test(braced(src, def.index + def[0].length - 1));
}

describe("the guard's own reading of a source file", () => {
  // The round-2 findings on the first version, as fixtures: an apostrophe
  // in a comment inside the tag; a `padding:` that is only a comment; a
  // one-line named object; a component that merely starts with ModalShell.
  const fixture = [
    "const panel = { padding: 18, display: \"flex\" };",
    "<ModalShell onClose={x} // the shell's default",
    "  panelStyle={{ display: \"flex\" }}>a</ModalShell>",
    "<ModalShell panelStyle={{ /* padding: none */ display: \"flex\" }}>b</ModalShell>",
    "<ModalShell panelStyle={panel}>c</ModalShell>",
    "<ModalShellX panelStyle={{ padding: 0 }}>d</ModalShellX>",
    "<ModalShell panelStyle={{ padding: 0 }} backdropStyle={{ paddingTop: 80 }}>e</ModalShell>",
  ].join("\n");
  const src = stripComments(fixture);
  const tags = openingTags(src);

  it("finds each tag once, whatever a comment inside it says, and not ModalShellX", () => {
    expect(tags.map((t) => t.slice(0, 20))).toEqual([
      "<ModalShell onClose=",
      "<ModalShell panelSty",
      "<ModalShell panelSty",
      "<ModalShell panelSty",
    ]);
  });

  it("reads padding from the panelStyle object or the named one-line object, never from a comment", () => {
    expect(tags.map((t) => decidesPadding(t, src))).toEqual([false, false, true, true]);
  });
});

describe("every <ModalShell> site decides its padding", () => {
  const files = tsxFiles(SRC).filter((f) => !f.endsWith("components/ModalShell.tsx"));
  const sites = files.flatMap((f) => {
    const src = stripComments(readFileSync(f, "utf8"));
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
