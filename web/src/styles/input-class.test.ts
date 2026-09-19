/**
 * `.input` is THE input chrome — one rule in base.css, not a name per surface.
 *
 * The sandbox modal shipped two fields with no chrome at all (browser default
 * inset grey), while the same look existed as `.kb-input` in kb.css and as an
 * inline copy in 57 other files. A field can only ship bare when there is
 * nothing shared to reach for, so this pins two things: the old name is gone,
 * and the files this change touched draw their inputs with the class, not an
 * inline copy. (The 113 inline copies elsewhere are a separate sweep.)
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(tsx?|css)$/.test(name)) out.push(full);
  }
  return out;
}

// The files this change re-skinned. Add a file here when you move it onto
// `.input`; the sweep of the rest is its own ticket.
const CONVERTED = ["components/ItemEnvironmentPanel.tsx", "components/ToolsChecklist.tsx"];

describe("the house input class", () => {
  it("is declared once, in base.css", () => {
    const base = readFileSync(resolve(HERE, "base.css"), "utf8");
    expect(base).toMatch(/^\.input \{/m);
    expect(base).toMatch(/\.input \{[^}]*border: 1px solid var\(--paper-3\)/);
    expect(base).toMatch(/\.input \{[^}]*border-radius: var\(--radius-btn\)/);
  });

  it("has replaced `.kb-input` everywhere — two names for one rule drift apart", () => {
    const hits = walk(SRC)
      .filter((f) => !f.endsWith(".test.ts") && !f.endsWith(".test.tsx"))
      .filter((f) => readFileSync(f, "utf8").includes("kb-input"))
      .map((f) => relative(SRC, f));
    expect(hits).toEqual([]);
  });

  it("dresses every <input> in the converted files, with no inline copy of the chrome", () => {
    for (const rel of CONVERTED) {
      const text = readFileSync(resolve(SRC, rel), "utf8");
      const inputs = text.match(/<input\b[\s\S]*?\/>/g) ?? [];
      expect(inputs.length, rel).toBeGreaterThan(0);
      for (const tag of inputs) {
        expect(tag, `${rel}: ${tag.slice(0, 60)}`).toMatch(/className=(\{`|")input\b/);
        // The class is the chrome; an inline border beside it is the copy
        // this rule exists to end. (Other elements — a segmented control's
        // wrapper — may still draw their own border; that is not an input.)
        expect(tag, `${rel}: ${tag.slice(0, 60)}`).not.toMatch(/border: "1px solid var\(--paper-3\)"/);
      }
    }
  });
});
