import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * #448 — the PM-app entity views (table / board / gantt / health) can't be
 * measured in happy-dom, so their look is guarded as a CSS drift check (same
 * approach as tokens.test.ts / kb-responsive.test.ts). This asserts the sheet
 * keeps the class contract the renderers depend on + stays token-clean; the
 * visual result itself is verified with Playwright against the real app.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(resolve(HERE, "entity-views.css"), "utf8");

describe("entity-views.css", () => {
  it("skins each view kind + shared write chrome", () => {
    for (const cls of [
      ".ev-panel",
      ".ev-table",
      ".ev-board",
      ".ev-card",
      ".ev-gantt__bar",
      ".ev-finding",
      ".ev-field",
      ".ev-quickcreate",
      ".ev-banner",
      ".ev-level",
      ".ev-empty",
    ]) {
      expect(CSS).toContain(cls);
    }
  });

  it("keeps the table calm — zebra + hover, chrome-on-interaction cells", () => {
    expect(CSS).toMatch(/\.ev-table tbody tr:nth-child\(even\)/);
    expect(CSS).toMatch(/\.ev-table tbody tr:hover/);
    // inline cell fields reveal their border only on hover/focus.
    expect(CSS).toMatch(/\.ev-table tbody \.ev-field\s*\{[^}]*border-color:\s*transparent/);
  });

  it("gives the board real columns with a drop-target + degraded state (§D)", () => {
    expect(CSS).toContain(".ev-board__col--over");
    expect(CSS).toContain(".ev-board__col--degraded");
  });

  it("distinguishes gantt task bars from the today marker by colour", () => {
    const today = CSS.match(/\.ev-gantt__today\s*\{[^}]*\}/)?.[0] ?? "";
    const bar = CSS.match(/\.ev-gantt__bar\s*\{[^}]*\}/)?.[0] ?? "";
    expect(today).toMatch(/--accent/); // the today line stays the one red signal
    expect(bar).not.toMatch(/--accent\b/); // bars must not reuse it, or they blend in
  });

  it("only references declared design tokens (no hardcoded brand colors)", () => {
    // Guard against a stray hex on a fill — everything routes through tokens.
    // Box-shadows are the one sanctioned rgba() exception (no shadow token).
    const withoutShadows = CSS.replace(/box-shadow:[^;]*;/g, "");
    expect(withoutShadows).not.toMatch(/:\s*#[0-9a-fA-F]{3,6}\b/);
  });
});
