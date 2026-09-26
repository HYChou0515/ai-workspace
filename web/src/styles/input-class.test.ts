/**
 * `.input` is THE input chrome — one rule in base.css, not a name per surface,
 * and (#829) EVERY text control wears it.
 *
 * #825 made the class and converted the two files it touched; the other 99
 * text controls kept dressing themselves — an inline copy of the border /
 * radius / surface, a scoped `.foo input { … }` rule, or nothing at all (the
 * browser's inset grey #825 was opened for). A field could ship bare with
 * every test green because nothing shared made "bare" visible. This guard
 * makes the class the only rule: it reads every `<input|textarea|select`
 * element in the JSX of every source under `src/` through the TypeScript
 * compiler's own parser (a regex scan of the first attempt could be made to
 * swallow the control after a tag holding a `// don't` comment) and fails on
 * any that neither wears the class nor sits in the whitelist below.
 *
 * Three shapes pass (docs/plan-input-class-sweep.md D1, D2):
 *   - `className="input"` / `"input xxx"` — the field itself is the box;
 *   - `className="input-group__field"` — a bare slot INSIDE an element that
 *     wears `input input-group` (a search box with its icon, a range with two
 *     `<time>` ends): the MUI / Ant / Radix shape. The one box with its own
 *     chrome is `.kb-composer` (the accent-bordered primary action, D7). The
 *     box is found by walking the slot's JSX ancestors, not by grepping the
 *     file;
 *   - a whitelist entry naming the file and the control, with the reason.
 *
 * The class is a STRING LITERAL on the tag (D11). A computed list
 * (`className={cond ? "input a" : "b"}`, a prop pass-through, a `{...spread}`
 * on the tag) is refused: a branch the guard cannot see is a branch that can
 * be bare. State goes on a `data-` attribute, as `.btn[data-active]` does.
 * The same applies to `style`: an object literal on the tag, or a `const`
 * object literal that is the ONE declaration of its name in the file
 * (`style={fieldSize}`, `{...fieldSize}`) — anything else (an import, a
 * call, a conditional, a name declared twice, a `let`, a quoted or computed
 * key) is refused, since a style the guard cannot read is a chrome it cannot
 * forbid. A box (any element wearing `input`) is held to the same rule.
 *
 * Not seen, said here: a control made with `React.createElement("input", …)`
 * (none exists; the reader walks JSX), and a slot handed to a component as a
 * prop (its box is wherever that component renders it). A file the parser
 * could not read is a failure, not a pass.
 */
import { mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
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

const isSource = (n: string) => n.endsWith(".tsx") && !n.endsWith(".test.tsx");
/** A view plugin's web half renders inside this SPA's document and wears its
 * classes, so its controls are held to the same rule (a gallery toolbar
 * shipped with bare native fields). Each plugin's own `web/src`, never its
 * node_modules. */
const PLUGINS = resolve(SRC, "../../view-plugins");
const PLUGIN_SOURCES = readdirSync(PLUGINS)
  .map((name) => join(PLUGINS, name, "web", "src"))
  .filter((dir) => {
    try {
      return statSync(dir).isDirectory();
    } catch {
      return false;
    }
  })
  .flatMap((dir) => walk(dir, isSource));
const SOURCES = [...walk(SRC, isSource), ...PLUGIN_SOURCES];
const SHEETS = walk(SRC, (n) => n.endsWith(".css"));
const BASE_CSS = readFileSync(resolve(HERE, "base.css"), "utf8");

/** `<input type="checkbox">` and friends are not text controls; `.input` is
 * the chrome of a box you type into. */
const NON_TEXT = new Set(["checkbox", "radio", "file", "range", "color", "hidden", "submit", "button", "image", "reset"]);
const CONTROL_TAGS = new Set(["input", "textarea", "select"]);
const classes = (list: string) => list.split(/\s+/).filter(Boolean);

type Control = {
  file: string;
  line: number;
  el: string;
  /** The class list when it is a string literal; "computed" for any expression or a spread on the tag; null when absent. */
  className: string | "computed" | null;
  /** The style's property names when readable; "opaque" when not (or a spread on the tag); null when absent. */
  style: string[] | "opaque" | null;
  /** The class lists of every JSX ancestor, nearest first ("computed" for an expression). */
  ancestors: (string | "computed" | null)[];
};
/** A box: any non-control JSX element whose literal class list includes `input`. */
type Box = { file: string; line: number; el: string; style: string[] | "opaque" | null };

function attrOf(el: ts.JsxOpeningLikeElement, name: string): ts.JsxAttribute | undefined {
  for (const a of el.attributes.properties) if (ts.isJsxAttribute(a) && a.name.getText() === name) return a;
  return undefined;
}
const hasSpread = (el: ts.JsxOpeningLikeElement) => el.attributes.properties.some((a) => ts.isJsxSpreadAttribute(a));

/** A string literal attribute value (`x="…"`, `x={"…"}`, `x={`…`}` with no
 * substitutions), else "computed", else null when the attribute is absent. */
function literalOf(el: ts.JsxOpeningLikeElement, name: string): string | "computed" | null {
  const a = attrOf(el, name);
  if (!a) return null;
  const v = a.initializer;
  if (!v) return "computed";
  if (ts.isStringLiteral(v)) return v.text;
  if (ts.isJsxExpression(v) && v.expression) {
    const e = v.expression;
    if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) return e.text;
  }
  return "computed";
}

/** Every variable declaration of a name anywhere in the file, with whether
 * it is a `const`. Resolution by NAME, not by scope: a name declared twice
 * (a sibling component's own `const input`) is refused rather than guessed. */
function declarationsOf(name: string, sf: ts.SourceFile): { init: ts.Expression | undefined; isConst: boolean }[] {
  const out: { init: ts.Expression | undefined; isConst: boolean }[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === name) {
      const list = node.parent;
      out.push({ init: node.initializer, isConst: ts.isVariableDeclarationList(list) && (list.flags & ts.NodeFlags.Const) !== 0 });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

/** The property names of an object literal, following `...spread` of a
 * `const` object literal declared once in the file; "opaque" for anything
 * the guard cannot read (an import, a call, a conditional, a `let`, a name
 * declared twice, a computed key). */
function styleKeys(expr: ts.Expression, sf: ts.SourceFile, depth = 0): string[] | "opaque" {
  if (depth > 4) return "opaque";
  if (ts.isParenthesizedExpression(expr) || ts.isAsExpression(expr) || ts.isSatisfiesExpression(expr)) return styleKeys(expr.expression, sf, depth);
  if (ts.isObjectLiteralExpression(expr)) {
    const keys: string[] = [];
    for (const p of expr.properties) {
      if (ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) {
        if (ts.isIdentifier(p.name)) keys.push(p.name.text);
        else if (ts.isStringLiteral(p.name)) keys.push(p.name.text);
        else return "opaque"; // a computed key names what it likes
      } else if (ts.isSpreadAssignment(p)) {
        const inner = styleKeys(p.expression, sf, depth + 1);
        if (inner === "opaque") return "opaque";
        keys.push(...inner);
      } else return "opaque";
    }
    return keys;
  }
  if (ts.isIdentifier(expr)) {
    const decls = declarationsOf(expr.text, sf);
    if (decls.length !== 1 || !decls[0].isConst || !decls[0].init) return "opaque";
    return styleKeys(decls[0].init, sf, depth + 1);
  }
  return "opaque";
}

function styleOf(node: ts.JsxOpeningLikeElement, sf: ts.SourceFile): string[] | "opaque" | null {
  if (hasSpread(node)) return "opaque";
  const styleAttr = attrOf(node, "style");
  if (!styleAttr) return null;
  const v = styleAttr.initializer;
  return v && ts.isJsxExpression(v) && v.expression ? styleKeys(v.expression, sf) : "opaque";
}

/** Every text-like control element in a source (and every box), with what
 * the guard needs to know about it, read from the TypeScript AST. A file the
 * parser could not read is reported, never skipped: a missing control would
 * be a silent pass. */
function readSource(file: string, raw: string): { controls: Control[]; boxes: Box[] } {
  const sf = ts.createSourceFile(file, raw, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const diagnostics = (sf as unknown as { parseDiagnostics?: unknown[] }).parseDiagnostics ?? [];
  if (diagnostics.length > 0) throw new Error(`${file}: the parser reports ${diagnostics.length} error(s); the guard cannot read it`);
  const controls: Control[] = [];
  const boxes: Box[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const el = node.tagName.getText(sf);
      const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
      const cls = literalOf(node, "className");
      if (CONTROL_TAGS.has(el)) {
        const type = literalOf(node, "type");
        if (!(typeof type === "string" && NON_TEXT.has(type))) {
          const ancestors: Control["ancestors"] = [];
          for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
            if (ts.isJsxElement(p)) ancestors.push(literalOf(p.openingElement, "className"));
          }
          controls.push({ file, line, el, className: hasSpread(node) ? "computed" : cls, style: styleOf(node, sf), ancestors });
        }
      } else if (typeof cls === "string" && classes(cls).includes("input")) {
        boxes.push({ file, line, el, style: styleOf(node, sf) });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return { controls, boxes };
}
const controlsInText = (file: string, raw: string) => readSource(file, raw).controls;

const READ = SOURCES.map((f) => readSource(relative(SRC, f), readFileSync(f, "utf8")));
const CONTROLS = READ.flatMap((r) => r.controls);
const BOXES = READ.flatMap((r) => r.boxes);

const isBox = (cls: string | "computed" | null) =>
  typeof cls === "string" && ((classes(cls).includes("input") && classes(cls).includes("input-group")) || classes(cls).includes("kb-composer"));

/** Dressed: the literal starts with `input` as a whole word, or names the slot. */
function wears(c: Control): boolean {
  if (c.className === null || c.className === "computed") return false;
  const list = classes(c.className);
  return list[0] === "input" || list.includes("input-group__field");
}
const isSlot = (c: Control) => typeof c.className === "string" && classes(c.className).includes("input-group__field");

/** Every class that rides beside `input` / `input-group` / the slot on ANY
 * JSX element — derived from the sources, so a new size class is covered the
 * day it is written, not when someone remembers to list it. These are the
 * classes whose rules may size but not re-draw. */
const MODIFIERS = new Set<string>();
for (const file of SOURCES) {
  const sf = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const cls = literalOf(node, "className");
      if (typeof cls === "string") {
        const list = classes(cls);
        if (list.includes("input") || list.includes("input-group") || list.includes("input-group__field")) {
          for (const c of list) if (c !== "input" && c !== "input-group" && c !== "input-group__field") MODIFIERS.add(c);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}

/**
 * The controls that stay bare on purpose (D1). `label` is an attribute
 * literal that appears on the tag and on no other bare control in the file;
 * an entry that matches no bare control is stale and fails — a whitelist
 * that outlives its reason is how "deliberately bare" turns back into
 * "forgot". The reason is part of the entry: an empty one is refused.
 */
const WHITELIST: { file: string; label: string; why: string }[] = [
  {
    file: "pages/investigation/TerminalPane.tsx",
    label: "terminal command",
    why: "a terminal has a prompt, not a field; a box around the command line is not a terminal",
  },
  {
    file: "pages/investigation/CommandPalette.tsx",
    label: "Go to file…",
    why: "the palette's header row, divided from the list by a rule (the Spotlight shape); a bordered field at the top of a bordered panel is a box in a box",
  },
  {
    file: "renderers/SheetGrid.tsx",
    label: "sheet-cell",
    why: "a spreadsheet cell: the grid draws the lines, the cell shows a border only while edited; a box per cell is a form, not a sheet",
  },
];

/** The chrome on a tag that also wears the class is the copy this rule ends —
 * the shorthands and their longhands, and a box-shadow drawn as a border.
 * Size and type (`width`, `height`, `padding`, `fontSize`, `fontFamily` for a
 * mono field of keys, `resize`) may stay inline — #829 §2 keeps "尺寸 /
 * 對齊". `borderColor` alone is a state (an active filter), not chrome. */
const INLINE_CHROME = new Set([
  "border", "borderTop", "borderRight", "borderBottom", "borderLeft", "borderWidth", "borderStyle", "borderRadius",
  "background", "backgroundColor", "backgroundImage", "boxShadow", "outline",
]);

/** In a stylesheet, the same set, with the values that REMOVE chrome allowed
 * (a slot, a table cell that shows its box only on hover). */
const CSS_CHROME =
  /(?:^|;)\s*(border|border-top|border-right|border-bottom|border-left|border-width|border-style|border-color|border-radius|background|background-color|background-image|box-shadow|outline)\s*:\s*([^;]+)/g;
const REMOVES_CHROME = /^(0|none|transparent)$/;

function rule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`(?:^|})\\s*${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
}

const where = (c: Control) => `${c.file}:${c.line} <${c.el}>`;

describe("the house input class", () => {
  it("is declared once, in base.css, with its companions (plan D3–D6, D9, D10)", () => {
    const base = BASE_CSS.replace(/\/\*[\s\S]*?\*\//g, "");
    const input = rule(base, ".input");
    expect(input, ".input").not.toBe("");
    expect(input).toMatch(/border: 1px solid var\(--paper-3\)/);
    expect(input).toMatch(/border-radius: var\(--radius-btn\)/);
    // The flex/<input> escape hatch every row relies on (TodoPanel's tests
    // point here): an <input> has an intrinsic min-content width, and
    // `min-width: auto` refuses to shrink past it.
    expect(input).toMatch(/flex: 1;/);
    expect(input).toMatch(/min-width: 0;/);
    // D10: focus is the accent border AND the global 2px `:focus-visible`
    // ring — the class never switches the outline off (base.css: "never to
    // none"). Chromium applies `:focus-visible` to a text field on click as
    // well, so mouse and keyboard look the same.
    expect(input).not.toMatch(/outline/);
    expect(rule(base, ".input:focus")).toMatch(/border-color: var\(--accent\)/);
    // D5: a textarea's padding is the element's, not a modifier to remember.
    expect(rule(base, "textarea.input")).toMatch(/padding: 8px 10px/);
    // D6: fill the width in a column — one modifier, not a scoped copy per form.
    const block = rule(base, ".input--block");
    expect(block).toMatch(/width: 100%/);
    expect(block).toMatch(/flex: none/);
    // D2: the group — the box wears `.input`, the control inside is a bare slot.
    expect(rule(base, ".input-group")).toMatch(/display: flex/);
    expect(rule(base, ".input-group:focus-within")).toMatch(/border-color: var\(--accent\)/);
    expect(rule(base, ".input-group:has(:focus-visible)")).toMatch(/outline: 2px solid var\(--accent\)/);
    const slot = rule(base, ".input-group__field");
    expect(slot, "the slot rule").not.toBe("");
    expect(slot).toMatch(/border: 0/);
    expect(slot).toMatch(/background: none/);
    expect(slot).toMatch(/outline: none/);
    // D3 / D4: the compact size is a SIZE, not a second chrome — and a chip's
    // width: `flex: none`, or `.input`'s `min-width: 0` lets it collapse to
    // its arrow in a tight row.
    const inline = rule(base, ".inline-edit");
    expect(inline, ".inline-edit").not.toBe("");
    expect(inline).toMatch(/min-height: 26px/);
    expect(inline).toMatch(/flex: none/);
    expect(inline).not.toMatch(/border:|background:|color:/);
    // D9 / D8: the disabled and invalid looks are the class's own. The invalid
    // field shows focus the way every field does (the ring); no second ring.
    expect(rule(base, ".input:disabled")).toMatch(/cursor: not-allowed/);
    expect(rule(base, '.input[aria-invalid="true"]')).toMatch(/border-color: var\(--err\)/);
    expect(base).not.toMatch(/aria-invalid="true"\]:focus/);
  });

  it("is named by no other sheet — its rules live in one place", () => {
    // `.kb-field .input { width: 100%; flex: none }` and two more like it were
    // three spellings of `.input--block`; a rule on `.input` in another sheet
    // is a rule nobody finds when the class changes.
    const hits: string[] = [];
    for (const sheet of SHEETS) {
      if (sheet.endsWith("base.css")) continue;
      const css = readFileSync(sheet, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const m of css.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
        const sel = m[1].trim().replace(/\s+/g, " ");
        if (/\.input(?![\w-])/.test(sel)) hits.push(`${relative(SRC, sheet)} → ${sel}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("has replaced `.kb-input` and `.kb-textarea` everywhere — two names for one rule drift apart", () => {
    const hits = walk(SRC, (n) => /\.(tsx?|css)$/.test(n) && !/\.test\.tsx?$/.test(n))
      .filter((f) => /kb-input|kb-textarea/.test(readFileSync(f, "utf8")))
      .map((f) => relative(SRC, f));
    expect(hits).toEqual([]);
  });

  it("reads the sources the way #829 counted them", () => {
    // Sanity-pin a few known shapes so the verdict below rests on a scan that
    // saw the tree.
    expect(CONTROLS.length).toBeGreaterThan(100);
    const files = new Set(CONTROLS.map((c) => c.file));
    expect(files.has("components/ItemEnvironmentPanel.tsx")).toBe(true);
    expect(CONTROLS.some((c) => c.el === "select")).toBe(true);
    expect(CONTROLS.some((c) => c.el === "textarea")).toBe(true);
    // and the view plugins' web halves, which draw into this document
    expect(files.has("../../view-plugins/chart/web/src/FacetGallery.tsx")).toBe(true);
    // The line a finding points at holds the tag's own `<input|textarea|select`.
    for (const c of CONTROLS) {
      const raw = readFileSync(resolve(SRC, c.file), "utf8").split("\n")[c.line - 1] ?? "";
      expect(raw, where(c)).toMatch(/<(input|textarea|select)\b/);
    }
  });

  it("keeps its own edges — what the reader counts and what it does not", () => {
    // Fixtures live in a temp dir, never under src/ (a fixture with a bare
    // control would fail the guard itself).
    const dir = mkdtempSync(join(tmpdir(), "input-class-"));
    const file = join(dir, "Edges.tsx");
    const src = [
      "/**",
      " * A docblock that mentions a <select> — not a control, and it must not",
      " * shift the line numbers below.",
      " */",
      "const size = { minHeight: 28 };",
      "const chrome = { ...size, border: \"1px solid red\" };",
      "export function Edges({ q }: { q: string }) {",
      "  return (",
      "    <div>",
      "      {/* <input /> in a JSX comment: not a control */}",
      '      <input className="input" placeholder="files to include — e.g. *.md, src/**" />',
      '      <input className="input" title="e.g. /*.md" />',
      '      <input aria-label="bare after globs" />',
      '      <input className="input" placeholder="a > b" onChange={(e) => q.length > 0 && e} />',
      '      <select className={q ? "input a" : "input"} />',
      '      <input type="checkbox" />',
      "      <input",
      '        className="input" // don\'t',
      "      />",
      '      <input aria-label="bare after an apostrophe" />',
      "      <input className=\"input\" onChange={(e) => /[\"]/.test(e.target.value) && e} />",
      '      <select aria-label="bare after a regex" />',
      '      <div className="input input-group"><input className="input-group__field" aria-label="slot in a box" /></div>',
      '      <div className="input-group"><input className="input-group__field" aria-label="slot in a box without input" /></div>',
      '      <input className="input-group__field" aria-label="slot outside any box" />',
      '      <input className="input" style={size} />',
      '      <input className="input" style={chrome} />',
      '      <input className="input" style={{ ...size, fontSize: 12 }} />',
      '      <input className="input" style={q ? size : chrome} />',
      '      <input className="input" style={{ "border": "1px solid red" }} />',
      '      <input className="input" style={{ ["boxShadow"]: "0 0 0 2px red" }} />',
      '      <input className="input input--block" {...{ style: { border: "1px solid red" } }} />',
      '      <input className="input" style={twice} />',
      '      <input className="input" style={changing} />',
      '      <div className="input input-group" style={{ border: "2px solid red" }}><input className="input-group__field" /></div>',
      "    </div>",
      "  );",
      "}",
      "export function Sibling() {",
      "  const twice = { border: \"3px solid red\" };",
      "  const inner = { minHeight: 20 };",
      "  return <input className=\"input\" style={{ ...twice, ...inner }} />;",
      "}",
      "let changing = { minHeight: 28 };",
      "changing = { border: \"1px solid red\" };",
      "const twice = { minHeight: 28 };",
    ].join("\n");
    writeFileSync(file, src);
    const found = controlsInText("Edges.tsx", readFileSync(file, "utf8"));
    // The docblock's and the JSX comment's do not count, the checkbox is not
    // text; a `/*` inside a string opens no comment; an apostrophe in a
    // trailing comment and a quote in a regex literal do not swallow the
    // control after them (a regex scan did both).
    expect(found.map((c) => `${c.line}:${c.el}`)).toEqual([
      "11:input", "12:input", "13:input", "14:input", "15:select", "17:input", "20:input", "21:input", "22:select",
      "23:input", "24:input", "25:input", "26:input", "27:input", "28:input", "29:input",
      "30:input", "31:input", "32:input", "33:input", "34:input", "35:input", "42:input",
    ]);
    expect(found.map((c) => wears(c))).toEqual([
      true, true, false, true, false, true, false, true, false, true, true, true, true, true, true, true,
      true, true, false, true, true, true, true,
    ]);
    expect(found[4].className).toBe("computed");
    // A slot is dressed only inside a box that wears `input input-group`.
    expect(found[9].ancestors[0]).toBe("input input-group");
    expect(found[10].ancestors.some(isBox)).toBe(false);
    expect(found[11].ancestors.some(isBox)).toBe(false);
    // `style`: an object literal or a same-file const is read; a spread of a
    // const carrying a border is a border; a conditional is opaque.
    expect(found[12].style).toEqual(["minHeight"]);
    expect(found[13].style).toEqual(["minHeight", "border"]);
    expect(found[14].style).toEqual(["minHeight", "fontSize"]);
    expect(found[15].style).toBe("opaque");
    // A quoted key is read by its text; a computed key names what it likes
    // and is opaque; a spread ON THE TAG hides both class and style; a name
    // declared twice in the file (a sibling component's own `twice`) or by
    // `let` is opaque rather than guessed; the sibling's in-function consts
    // are read when they are the one declaration of their name.
    expect(found[16].style).toEqual(["border"]);
    expect(found[17].style).toBe("opaque");
    expect(found[18].className).toBe("computed");
    expect(found[18].style).toBe("opaque");
    expect(found[19].style).toBe("opaque");
    expect(found[20].style).toBe("opaque");
    expect(found[22].style).toBe("opaque");
    // A box is read too: the div at line 35 is a box carrying a border (the
    // div at 24 wears `input-group` without `input` — not a box, not read).
    const boxes = readSource("Edges.tsx", readFileSync(file, "utf8")).boxes;
    expect(boxes.map((b) => `${b.line}:${b.style}`)).toEqual(["23:null", "35:border"]);
    // A file the parser cannot read is a failure, not a pass.
    const broken = join(dir, "Broken.tsx");
    writeFileSync(broken, 'export function B() { return <input aria-label="lost" /> }\n/* never closed');
    expect(() => readSource("Broken.tsx", readFileSync(broken, "utf8"))).toThrow(/parser reports/);
  });

  it("dresses every text control with the class, or names it in the whitelist with a reason", () => {
    const bare = CONTROLS.filter((c) => !wears(c));
    const excused = new Set<Control>();
    for (const w of WHITELIST) {
      expect(w.why.length, `${w.file}: a whitelist entry carries its reason`).toBeGreaterThan(20);
      const hits = bare.filter((c) => c.file === w.file && readFileSync(resolve(SRC, c.file), "utf8").split("\n")
        .slice(c.line - 1, c.line + 20).join("\n").includes(w.label));
      expect(hits.length, `whitelist entry ${w.file} "${w.label}" must match exactly one bare control (stale?)`).toBe(1);
      excused.add(hits[0]);
    }
    const offenders = bare
      .filter((c) => !excused.has(c))
      .map((c) => `${where(c)}${c.className === "computed" ? " (computed className — write the literal; state goes on a data- attribute)" : ""}`);
    expect(offenders, `${offenders.length} text control(s) without className="input"`).toEqual([]);
  });

  it("does not let a dressed control, or a box, carry an inline copy of the chrome", () => {
    // A `style` the guard cannot read (an import, a call, a conditional, a
    // spread on the tag, a name declared twice) is refused outright — a
    // chrome it cannot see is a chrome it cannot forbid. A box (an element
    // wearing `input` around a slot) is held to the same rule: it IS the
    // field's chrome.
    const copies: string[] = [];
    const check = (label: string, style: string[] | "opaque" | null) => {
      if (style === null) return;
      if (style === "opaque") copies.push(`${label} style is not an object literal or the one same-file const of its name`);
      else for (const k of style) if (INLINE_CHROME.has(k)) copies.push(`${label} style.${k}`);
    };
    for (const c of CONTROLS) if (wears(c)) check(where(c), c.style);
    for (const b of BOXES) check(`${b.file}:${b.line} <${b.el}> (box)`, b.style);
    expect(copies).toEqual([]);
  });

  it("keeps every slot inside a box that draws it", () => {
    // A slot draws nothing, so it is bare unless an ANCESTOR wears
    // `input input-group` (or is the composer, D7). Found by walking the JSX
    // tree, not by grepping the file: a box in the same file three rows up
    // draws nothing around this row.
    const loose = CONTROLS.filter((c) => isSlot(c) && !c.ancestors.some(isBox)).map(where);
    expect(loose).toEqual([]);
  });

  it("keeps the composer's box and the doc search's floor — the two boxes the differential cannot stand in for", () => {
    // `.kb-composer` is the one box the slot rule accepts by NAME (D7): if its
    // rule lost its border, the composer's slot would be bare with every
    // other test green. Its ring is D10's; the doc search's floor is what
    // stops `.input`'s `min-width: 0` shrinking it to its icon (P7).
    const kb = readFileSync(resolve(HERE, "kb.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const composer = rule(kb, ".kb-composer");
    expect(composer, ".kb-composer").toMatch(/border: 1.5px solid var\(--accent\)/);
    expect(composer).toMatch(/background: var\(--white\)/);
    expect(rule(kb, ".kb-composer:has(:focus-visible)")).toMatch(/outline: 2px solid var\(--accent\)/);
    expect(rule(kb, ".kb-docsearch--inline")).toMatch(/min-width: 160px/);
  });

  it("lets no scoped rule re-draw the chrome — a second rule is a second look", () => {
    // Any rule whose selector names input / textarea / select as an ELEMENT,
    // or names one of the classes that ride beside `input` on a tag (derived
    // above), must not declare a border / radius / surface / shadow — `0`,
    // `none` and `transparent` excepted, since they REMOVE chrome (a slot, a
    // table cell that shows its box only on hover), and state selectors
    // (:hover / :focus…) excepted, since they toggle a chrome rather than
    // draw a second one (a `:hover` rule COULD draw a whole second chrome;
    // none does, and this check would not see it). base.css's theme reset
    // (`input, textarea, select { … background-color }`) is the one element
    // rule allowed a surface: it flips the UA white with the theme, and
    // `.input` overrides it. Not seen either: a rule keyed on an id, a data
    // attribute or a universal child (`#q-count`, `[data-testid=…]`,
    // `.kb-field > *`) — none targets a control today; it would be the same
    // "second look" and belongs here if one appears.
    expect(MODIFIERS.size).toBeGreaterThan(10);
    expect(MODIFIERS.has("inline-edit")).toBe(true);
    expect(MODIFIERS.has("ev-field")).toBe(true);
    const modifier = new RegExp(`\\.(${[...MODIFIERS].map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\w-])`);
    const ELEMENT = /(^|[\s,>+~])(input|textarea|select)(?![\w-])/;
    const STATE = /:(hover|focus|focus-visible|focus-within|active|disabled)(?![\w-])/;
    const offenders: string[] = [];
    for (const sheet of SHEETS) {
      const isBase = sheet.endsWith("base.css");
      const css = readFileSync(sheet, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      const rx = /([^{}]+)\{([^{}]*)\}/g;
      let m: RegExpExecArray | null;
      while ((m = rx.exec(css))) {
        const sel = m[1].trim().replace(/\s+/g, " ");
        if (sel.startsWith("@")) continue;
        if (sel === "input, textarea, select" && isBase) continue;
        if (/type="(checkbox|radio|range|file)"|\.switch(?![\w-])|::-webkit-/.test(sel)) continue;
        if (!ELEMENT.test(sel) && !modifier.test(sel)) continue;
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
