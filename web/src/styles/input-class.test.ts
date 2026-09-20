/**
 * `.input` is THE input chrome — one rule in base.css, not a name per surface,
 * and (#829) EVERY text control wears it.
 *
 * #825 made the class and converted the two files it touched; the other 98
 * text controls kept dressing themselves — an inline copy of the border /
 * radius / surface, a scoped `.foo input { … }` rule, or nothing at all (the
 * browser's inset grey #825 was opened for). A field could ship bare with
 * every test green because nothing shared made "bare" visible. This guard
 * makes the class the only rule: it scans every `<input|textarea|select`
 * opening tag under `src/` the way #829 counted them (bracket-aware, across
 * lines, comments stripped, non-text types skipped) and fails on any tag that
 * neither wears the class nor sits in the whitelist below.
 *
 * Three shapes pass:
 *   - `className="input"` / `"input xxx"` / a `{…}` expression whose string
 *     literals say `input …` — the field itself is the box;
 *   - `className="input-group__field"` — a bare slot inside a box that wears
 *     `.input.input-group` (a search box with its icon, a range with two
 *     `<time>` ends), the Bootstrap input-group shape. The one box with its
 *     own chrome is `.kb-composer` (the accent-bordered primary action);
 *   - a whitelist entry naming the file and the control, with the reason.
 *
 * A prop pass-through (`className={className}`) does NOT pass: the scan can
 * only see the tag, so the literal has to be on the tag.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");

function walk(dir: string, keep: (name: string) => boolean): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full, keep));
    else if (keep(name)) out.push(full);
  }
  return out;
}

const SOURCES = walk(SRC, (n) => n.endsWith(".tsx") && !n.endsWith(".test.tsx"));
const SHEETS = walk(SRC, (n) => n.endsWith(".css"));

/** `<input type="checkbox">` and friends are not text controls; `.input` is
 * the chrome of a box you type into. */
const NON_TEXT = /\btype=\{?["'`](checkbox|radio|file|range|color|hidden|submit|button|image|reset)["'`]/;

/** Strip `/* … *​/` blocks and whole-line `//` comments so a `<select>` in a
 * docstring is not a control (#829's count had three of those). Strings and
 * JSX text keep their `//` (a URL is not a comment). */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

type Control = { file: string; line: number; el: string; tag: string };

/** Every text-like `<input|textarea|select …>` opening tag in a source, with
 * the tag's full text. A tag ends at the first `>` outside `{…}` and outside
 * a string — an arrow function in `onChange` has `=>` inside braces. */
function controlsIn(file: string): Control[] {
  const text = stripComments(readFileSync(file, "utf8"));
  const out: Control[] = [];
  const open = /<(input|textarea|select)\b/g;
  let m: RegExpExecArray | null;
  while ((m = open.exec(text))) {
    let depth = 0;
    let quote: string | null = null;
    let j = m.index + m[0].length;
    for (; j < text.length; j++) {
      const c = text[j];
      if (quote) {
        if (c === quote && text[j - 1] !== "\\") quote = null;
        continue;
      }
      if (c === '"' || c === "'" || c === "`") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    const tag = text.slice(m.index, j + 1);
    open.lastIndex = j + 1;
    if (NON_TEXT.test(tag)) continue;
    out.push({ file: relative(SRC, file), line: text.slice(0, m.index).split("\n").length, el: m[1], tag });
  }
  return out;
}

const CONTROLS = SOURCES.flatMap(controlsIn);

/** The class is on the tag: a string / template literal that starts with
 * `input` as a whole word, or a `{…}` expression whose literals do. */
function wears(tag: string): boolean {
  const attr = tag.match(/\bclassName=(?:"([^"]*)"|\{([\s\S]*?)\}(?=\s|\/|>))/);
  if (!attr) return false;
  const literal = attr[1] !== undefined ? attr[1] : attr[2];
  if (attr[1] !== undefined) return /^input(\s|$)/.test(literal) || /(^|\s)input-group__field(\s|$)/.test(literal);
  // `{`input ${x}`}` / `{cond ? "input a" : "input b"}` / `{cx("input", …)}`:
  // every string literal inside must be a class list, and one of them must
  // start with `input` or name the slot.
  const strings = [...literal.matchAll(/["'`]([^"'`]*)["'`]/g)].map((s) => s[1]);
  return strings.some((s) => /^input(\s|$)/.test(s) || /(^|\s)input-group__field(\s|$)/.test(s));
}

/**
 * The controls that stay bare on purpose. `label` is a literal that appears
 * on the tag and nowhere else in the file's controls; an entry that matches
 * no bare control is stale and fails — a whitelist that outlives its reason
 * is how "deliberately bare" turns back into "forgot".
 */
const WHITELIST: { file: string; label: string; why: string }[] = [
  {
    file: "pages/investigation/TerminalPane.tsx",
    label: 'aria-label="terminal command"',
    why: "a terminal has a prompt, not a field; a box around the command line is not a terminal",
  },
  {
    file: "pages/investigation/CommandPalette.tsx",
    label: 'placeholder="Go to file…"',
    why: "the palette's header row, divided from the list by a rule (the Spotlight shape); a bordered field at the top of a bordered panel is a box in a box",
  },
];

/** The chrome on a tag that also wears the class is the copy this rule ends.
 * Size (`width`, `height`, `padding`, `fontSize`, `resize`) may stay inline —
 * #829 §2 keeps "尺寸 / 對齊". `borderColor` alone is a state (an active
 * filter, an invalid field), not chrome. */
const INLINE_CHROME = /\b(border|borderRadius|background|outline|fontFamily)\s*:/;

describe("the house input class", () => {
  it("is declared once, in base.css, with the textarea / block / group companions", () => {
    const base = readFileSync(resolve(HERE, "base.css"), "utf8");
    expect(base).toMatch(/^\.input \{/m);
    expect(base).toMatch(/\.input \{[^}]*border: 1px solid var\(--paper-3\)/);
    expect(base).toMatch(/\.input \{[^}]*border-radius: var\(--radius-btn\)/);
    // A textarea with `padding: 0 10px` has its first line on the border.
    expect(base).toMatch(/^textarea\.input \{[^}]*padding: 8px 10px/m);
    // In a column `flex: 1` is the wrong axis; in a block parent an input is
    // its UA width. One modifier, not a scoped copy per form.
    expect(base).toMatch(/^\.input--block \{[^}]*width: 100%[^}]*flex: none/m);
    // The group: the box wears `.input`, the control inside is a bare slot.
    expect(base).toMatch(/^\.input-group \{[^}]*display: flex/m);
    expect(base).toMatch(/^\.input-group:focus-within \{[^}]*border-color: var\(--accent\)/m);
    const slot = base.match(/^\.input-group__field \{[^}]*\}/m)?.[0] ?? "";
    expect(slot, "the slot rule").not.toBe("");
    expect(slot).toMatch(/border: 0/);
    expect(slot).toMatch(/background: none/);
    expect(slot).toMatch(/outline: none/);
    // The compact modifier is a SIZE, not a second chrome.
    const inline = base.match(/^\.inline-edit \{[^}]*\}/m)?.[0] ?? "";
    expect(inline, ".inline-edit rule").not.toBe("");
    expect(inline).toMatch(/min-height: 26px/);
    expect(inline).not.toMatch(/border:|background:|color:/);
  });

  it("has replaced `.kb-input` and `.kb-textarea` everywhere — two names for one rule drift apart", () => {
    const hits = walk(SRC, (n) => /\.(tsx?|css)$/.test(n) && !/\.test\.tsx?$/.test(n))
      .filter((f) => /kb-input|kb-textarea/.test(readFileSync(f, "utf8")))
      .map((f) => relative(SRC, f));
    expect(hits).toEqual([]);
  });

  it("scans the sources the way #829 counted them", () => {
    // The scan itself has to be trusted before its verdict is: it sees across
    // lines, past `=>` inside braces, and skips the non-text types and the
    // tags in comments. Sanity-pin a few known shapes.
    expect(CONTROLS.length).toBeGreaterThan(100);
    const files = new Set(CONTROLS.map((c) => c.file));
    expect(files.has("components/ItemEnvironmentPanel.tsx")).toBe(true);
    expect(CONTROLS.some((c) => c.el === "select")).toBe(true);
    expect(CONTROLS.some((c) => c.el === "textarea")).toBe(true);
    expect(CONTROLS.some((c) => /type="checkbox"/.test(c.tag))).toBe(false);
    // `roleWidget.tsx` mentions `<input type=date>` in a docstring; that is
    // not a control.
    expect(CONTROLS.filter((c) => c.file === "renderers/entity/roleWidget.tsx" && /type=date>/.test(c.tag))).toEqual([]);
  });

  it("dresses every text control with the class, or names it in the whitelist with a reason", () => {
    const bare = CONTROLS.filter((c) => !wears(c.tag));
    const excused = new Set<Control>();
    for (const w of WHITELIST) {
      const hits = bare.filter((c) => c.file === w.file && c.tag.includes(w.label));
      expect(hits.length, `whitelist entry ${w.file} ${w.label} must match exactly one bare control (stale?)`).toBe(1);
      excused.add(hits[0]);
    }
    const offenders = bare.filter((c) => !excused.has(c)).map((c) => `${c.file}:${c.line} <${c.el}>`);
    expect(offenders, `${offenders.length} text control(s) without className="input"`).toEqual([]);
  });

  it("does not let a dressed control carry an inline copy of the chrome", () => {
    const copies = CONTROLS.filter((c) => wears(c.tag) && INLINE_CHROME.test(c.tag)).map(
      (c) => `${c.file}:${c.line} <${c.el}> ${c.tag.match(INLINE_CHROME)?.[0]}`,
    );
    expect(copies).toEqual([]);
  });

  it("keeps a slot inside a box that draws it", () => {
    // The slot class is the one hole in the rule above — a slot in a box that
    // draws no chrome is bare and passes. So every file that uses a slot must
    // also put the box beside it: `.input.input-group`, or the composer.
    for (const file of SOURCES) {
      const text = readFileSync(file, "utf8");
      if (!text.includes("input-group__field")) continue;
      expect(
        /input input-group|kb-composer/.test(text),
        `${relative(SRC, file)}: a slot without an .input.input-group (or .kb-composer) box`,
      ).toBe(true);
    }
  });

  it("lets no scoped rule re-draw the chrome — a second rule is a second look", () => {
    // Any rule whose selector names input / textarea / select as an ELEMENT,
    // or one of the modifier classes that ride on `.input`, must not declare
    // a border / border-radius / background — `0`, `none` and `transparent`
    // excepted, since they REMOVE chrome (a slot, a table cell that shows its
    // box only on hover), and state selectors (:hover / :focus…) excepted,
    // since they toggle a chrome rather than draw a second one. base.css's
    // theme reset (`input, textarea, select { … background-color }`) is the
    // one element rule allowed a surface: it flips the UA white with the
    // theme, and `.input` overrides it.
    const MODIFIERS =
      /\.(inline-edit|input--block|ev-field|ev-select|ev-viewpanel__range|kb-docsearch|rvw__search|kb-cards__(search-input|title|term)|rvw-drawer__(addkey|answer)|kb-att__rename|gbr__(search|kind|collection)|manage-chats__(search|rename)|chat-rail__rename|kb-colpage__(nameedit|descedit)|kb-chats__rename|kb-composer__input|export-dialog__num)(?![\w-])/;
    const ELEMENT = /(^|[\s,>+~])(input|textarea|select)(?![\w-])/;
    const STATE = /:(hover|focus|focus-visible|focus-within|active|disabled)(?![\w-])/;
    const offenders: string[] = [];
    for (const sheet of SHEETS) {
      const css = readFileSync(sheet, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      const rule = /([^{}]+)\{([^{}]*)\}/g;
      let m: RegExpExecArray | null;
      while ((m = rule.exec(css))) {
        const sel = m[1].trim().replace(/\s+/g, " ");
        if (sel.startsWith("@")) continue;
        if (sel === "input, textarea, select" && sheet.endsWith("base.css")) continue;
        if (/type="(checkbox|radio|range|file)"|\.switch(?![\w-])|::-webkit-/.test(sel)) continue;
        if (!ELEMENT.test(sel) && !MODIFIERS.test(sel)) continue;
        if (STATE.test(sel)) continue;
        if (/\.input-group__field(?![\w-])/.test(sel)) continue; // the slot reset itself
        for (const d of m[2].matchAll(/(?:^|;)\s*(border|border-radius|background)\s*:\s*([^;]+)/g)) {
          if (d[1] !== "border-radius" && /^(0|none|transparent)$/.test(d[2].trim())) continue;
          offenders.push(`${relative(SRC, sheet)} → ${sel} { … ${d[1]}: ${d[2].trim()} … }`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
