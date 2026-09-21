/**
 * `.input` is THE input chrome — one rule in base.css, not a name per surface,
 * and (#829) EVERY text control wears it.
 *
 * #825 made the class and converted the two files it touched; the other 99
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
 *   - `className="input"` / `"input xxx"` — the field itself is the box;
 *   - `className="input-group__field"` — a bare slot inside a box that wears
 *     `.input.input-group` (a search box with its icon, a range with two
 *     `<time>` ends), the Bootstrap input-group shape. The one box with its
 *     own chrome is `.kb-composer` (the accent-bordered primary action);
 *   - a whitelist entry naming the file and the control, with the reason.
 *
 * The class is a STRING LITERAL on the tag. A computed list
 * (`className={cond ? "input a" : "b"}`, a prop pass-through) is refused: the
 * scan can only read the tag, and a branch it cannot see is a branch that
 * can be bare. State goes on a `data-` attribute, as `.btn[data-active]` does.
 */
import { mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
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

/** Blank out `/* … *​/` blocks and whole-line `//` comments so a `<select>` in
 * a docstring is not a control (#829's count had three of those) — keeping
 * every newline, so a line number read off the result is the file's own. A
 * block comment opens only at the start of a line, or after `{` / `(` (a JSX
 * comment, an argument): a placeholder that says `src/**` or `e.g. /*.md`
 * would otherwise open one that swallows the next control. A mid-line `/*`
 * after code is NOT stripped, and a `<select>` inside such a comment counts
 * as a control — the error shows, rather than hiding a bare one. */
function stripComments(text: string): string {
  return text
    .replace(/(?<=^[ \t]*|\{\s*|\(\s*)\/\*[\s\S]*?\*\//gm, (m) => "\n".repeat(m.split("\n").length - 1))
    .replace(/^[ \t]*\/\/.*$/gm, "");
}

type Control = { file: string; line: number; el: string; tag: string };

/** Every text-like `<input|textarea|select …>` opening tag in a source text,
 * with the tag's full text. A tag ends at the first `>` outside `{…}` and
 * outside a string — an arrow function in `onChange` has `=>` inside braces. */
function controlsInText(file: string, raw: string): Control[] {
  const text = stripComments(raw);
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
    out.push({ file, line: text.slice(0, m.index).split("\n").length, el: m[1], tag });
  }
  return out;
}

const controlsIn = (file: string) => controlsInText(relative(SRC, file), readFileSync(file, "utf8"));
const CONTROLS = SOURCES.flatMap(controlsIn);

/** The tag's class list, when it is a string literal; `"computed"` when it is
 * a `{…}` expression; `null` when there is none. */
function classOf(tag: string): string | "computed" | null {
  const attr = tag.match(/\bclassName=(?:"([^"]*)"|\{)/);
  if (!attr) return null;
  return attr[1] === undefined ? "computed" : attr[1];
}

/** Dressed: the literal starts with `input` as a whole word, or names the slot. */
function wears(tag: string): boolean {
  const cls = classOf(tag);
  if (cls === null || cls === "computed") return false;
  return /^input(\s|$)/.test(cls) || /(^|\s)input-group__field(\s|$)/.test(cls);
}

/** Every class that rides beside `input` / `input-group` / the slot on ANY
 * element (a control, a box such as `.kb-docsearch`) — derived from the sources, so a
 * new size class is covered the day it is written, not when someone remembers
 * to list it. These are the classes whose rules may size but not re-draw. */
const MODIFIERS = new Set<string>();
for (const file of SOURCES) {
  for (const m of readFileSync(file, "utf8").matchAll(/\bclassName="([^"]*)"/g)) {
    const list = m[1].split(/\s+/).filter(Boolean);
    if (!list.includes("input") && !list.includes("input-group") && !list.includes("input-group__field")) continue;
    for (const c of list) if (c !== "input" && c !== "input-group" && c !== "input-group__field") MODIFIERS.add(c);
  }
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
  {
    file: "renderers/SheetGrid.tsx",
    label: 'className="sheet-cell"',
    why: "a spreadsheet cell: the grid draws the lines, the cell shows a border only while edited; a box per cell is a form, not a sheet",
  },
];

/** The chrome on a tag that also wears the class is the copy this rule ends —
 * the shorthands and their longhands, and a box-shadow drawn as a border.
 * Size and type (`width`, `height`, `padding`, `fontSize`, `fontFamily` for a
 * mono field of keys, `resize`) may stay inline — #829 §2 keeps "尺寸 /
 * 對齊". `borderColor` alone is a state (an active filter), not chrome. */
const INLINE_CHROME =
  /\b(border|borderTop|borderRight|borderBottom|borderLeft|borderWidth|borderStyle|borderRadius|background|backgroundColor|backgroundImage|boxShadow|outline)\s*:/;

/** In a stylesheet, the same set, with the values that REMOVE chrome allowed
 * (a slot, a table cell that shows its box only on hover). */
const CSS_CHROME = /(?:^|;)\s*(border|border-top|border-right|border-bottom|border-left|border-width|border-style|border-color|border-radius|background|background-color|background-image|box-shadow|outline)\s*:\s*([^;]+)/g;
const REMOVES_CHROME = /^(0|none|transparent)$/;

describe("the house input class", () => {
  it("is declared once, in base.css, with the textarea / block / group companions", () => {
    const base = readFileSync(resolve(HERE, "base.css"), "utf8");
    expect(base).toMatch(/^\.input \{/m);
    expect(base).toMatch(/\.input \{[^}]*border: 1px solid var\(--paper-3\)/);
    expect(base).toMatch(/\.input \{[^}]*border-radius: var\(--radius-btn\)/);
    // The flex/<input> escape hatch every row relies on (TodoPanel's tests
    // point here): an <input> has an intrinsic min-content width, and
    // `min-width: auto` refuses to shrink past it.
    expect(base).toMatch(/\.input \{[^}]*flex: 1;[^}]*min-width: 0;/);
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
    // The compact modifier is a SIZE, not a second chrome — and a chip's
    // width: `flex: none`, or `.input`'s `min-width: 0` lets it collapse to
    // its arrow in a tight row.
    const inline = base.match(/^\.inline-edit \{[^}]*\}/m)?.[0] ?? "";
    expect(inline, ".inline-edit rule").not.toBe("");
    expect(inline).toMatch(/min-height: 26px/);
    expect(inline).toMatch(/flex: none/);
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
    // The line a finding points at is the FILE's line, not the line in the
    // comment-stripped text (a leading docblock put it 267 lines out once):
    // every reported line holds the tag's own `<input|textarea|select`.
    for (const c of CONTROLS) {
      const raw = readFileSync(resolve(SRC, c.file), "utf8").split("\n")[c.line - 1] ?? "";
      expect(raw, `${c.file}:${c.line}`).toMatch(/<(input|textarea|select)\b/);
    }
  });

  it("keeps its own edges — what the scanner counts and what it does not", () => {
    // Fixtures live in a temp dir, never under src/ (a fixture with a bare
    // control would fail the guard itself).
    const dir = mkdtempSync(join(tmpdir(), "input-class-"));
    const file = join(dir, "Edges.tsx");
    const src = [
      "/**",
      " * A docblock that mentions a <select> — not a control, and it must not",
      " * shift the line numbers below.",
      " */",
      'export function Edges({ q }: { q: string }) {',
      "  return (",
      "    <div>",
      "      {/* <input /> in a JSX comment: not a control */}",
      '      <input className="input" placeholder="files to include — e.g. *.md, src/**" />',
      '      <input className="input" title="e.g. /*.md" />',
      '      <input aria-label="bare after globs" />',
      '      <input className="input" placeholder="a > b" onChange={(e) => q.length > 0 && e} />',
      '      <select className={q ? "input a" : "input"} />',
      '      <input type="checkbox" />',
      "    </div>",
      "  );",
      "}",
    ].join("\n");
    writeFileSync(file, src);
    const found = controlsInText("Edges.tsx", readFileSync(file, "utf8"));
    // Six real controls (the docblock's and the JSX comment's do not count,
    // the checkbox is not text); a `/*` inside a string opens no comment, so
    // the bare one after the globs is seen.
    expect(found.map((c) => `${c.line}:${c.el}`)).toEqual(["9:input", "10:input", "11:input", "12:input", "13:select"]);
    expect(found.map((c) => wears(c.tag))).toEqual([true, true, false, true, false]);
    expect(classOf(found[4].tag)).toBe("computed");
    // A `>` inside a string or an arrow inside braces does not end the tag.
    expect(found[3].tag).toContain("onChange");
  });

  it("dresses every text control with the class, or names it in the whitelist with a reason", () => {
    const bare = CONTROLS.filter((c) => !wears(c.tag));
    const excused = new Set<Control>();
    for (const w of WHITELIST) {
      const hits = bare.filter((c) => c.file === w.file && c.tag.includes(w.label));
      expect(hits.length, `whitelist entry ${w.file} ${w.label} must match exactly one bare control (stale?)`).toBe(1);
      excused.add(hits[0]);
    }
    const offenders = bare
      .filter((c) => !excused.has(c))
      .map((c) => `${c.file}:${c.line} <${c.el}>${classOf(c.tag) === "computed" ? " (computed className — write the literal)" : ""}`);
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
    // names `.input` outside base.css, or names one of the classes that ride
    // beside `input` on a tag (derived above), must not declare a border /
    // radius / surface / shadow — `0`, `none` and `transparent` excepted,
    // since they REMOVE chrome (a slot, a table cell that shows its box only
    // on hover), and state selectors (:hover / :focus…) excepted, since they
    // toggle a chrome rather than draw a second one. base.css's theme reset
    // (`input, textarea, select { … background-color }`) is the one element
    // rule allowed a surface: it flips the UA white with the theme, and
    // `.input` overrides it. Not seen: a rule keyed on an id or a data
    // attribute (`#q-count`, `[data-testid=…]`) — none targets a control
    // today; it would be the same "second look" and belongs here if one
    // appears.
    expect(MODIFIERS.size).toBeGreaterThan(10);
    expect(MODIFIERS.has("inline-edit")).toBe(true);
    expect(MODIFIERS.has("ev-field")).toBe(true);
    expect(MODIFIERS.has("kb-docsearch")).toBe(true);
    const modifier = new RegExp(`\\.(${[...MODIFIERS].map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\w-])`);
    const ELEMENT = /(^|[\s,>+~])(input|textarea|select)(?![\w-])/;
    const STATE = /:(hover|focus|focus-visible|focus-within|active|disabled)(?![\w-])/;
    const offenders: string[] = [];
    for (const sheet of SHEETS) {
      const isBase = sheet.endsWith("base.css");
      const css = readFileSync(sheet, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      const rule = /([^{}]+)\{([^{}]*)\}/g;
      let m: RegExpExecArray | null;
      while ((m = rule.exec(css))) {
        const sel = m[1].trim().replace(/\s+/g, " ");
        if (sel.startsWith("@")) continue;
        if (sel === "input, textarea, select" && isBase) continue;
        if (/type="(checkbox|radio|range|file)"|\.switch(?![\w-])|::-webkit-/.test(sel)) continue;
        const namesInput = /\.input(?![\w-])/.test(sel) && !isBase;
        if (!ELEMENT.test(sel) && !modifier.test(sel) && !namesInput) continue;
        if (STATE.test(sel)) continue;
        if (/\.input-group__field(?![\w-])/.test(sel)) continue; // the slot reset itself
        for (const d of m[2].matchAll(CSS_CHROME)) {
          if (d[1] !== "border-radius" && REMOVES_CHROME.test(d[2].trim())) continue;
          offenders.push(`${relative(SRC, sheet)} → ${sel} { … ${d[1]}: ${d[2].trim()} … }`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
