/**
 * A view plugin's buttons wear the house button, `.btn` with its
 * `data-variant` and `data-size` (base.css; `components/Btn.tsx` is the same
 * class for the SPA's own code).
 *
 * base.css resets every `<button>` to bare text (`background: none; border:
 * 0`), so a button that does not wear the class reads as a word, not a
 * control: the facet gallery's "Select ranks" / "Clear selection" / its sort
 * button, the chart's Refresh and the marking control's Link all shipped that
 * way, with every test green. A plugin cannot import `Btn` (it sees only the
 * SDK), but it renders into this document, so the class is what it wears.
 *
 * Scope: every `.tsx` under each plugin's `web/src` (tests excepted), and the
 * view header's marking control, which sits beside them. The SPA's own
 * buttons are not in scope here: dozens are deliberate icon or row buttons
 * with their own rules, and this guard is for the plugin surface. Like the
 * input guard, the class and both attributes must be string literals on the
 * tag: a computed value is a branch this guard cannot see.
 */
import { mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");
const PLUGINS = resolve(SRC, "../../view-plugins");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (name.endsWith(".tsx") && !name.endsWith(".test.tsx")) out.push(full);
  }
  return out;
}

const PLUGIN_DIRS = readdirSync(PLUGINS)
  .map((name) => join(PLUGINS, name, "web", "src"))
  .filter((dir) => {
    try {
      return statSync(dir).isDirectory();
    } catch {
      return false;
    }
  });
const SOURCES = [...PLUGIN_DIRS.flatMap(walk), resolve(SRC, "renderers/entity/MarkingControl.tsx")];

type Button = { where: string; className: string | "computed" | null; variant: string | "computed" | null; size: string | "computed" | null };

function literal(el: ts.JsxOpeningLikeElement, name: string): string | "computed" | null {
  const a = el.attributes.properties.find((p) => ts.isJsxAttribute(p) && p.name.getText() === name) as ts.JsxAttribute | undefined;
  if (!a) return el.attributes.properties.some((p) => ts.isJsxSpreadAttribute(p)) ? "computed" : null;
  const v = a.initializer;
  if (!v) return "computed";
  if (ts.isStringLiteral(v)) return v.text;
  if (ts.isJsxExpression(v) && v.expression && (ts.isStringLiteral(v.expression) || ts.isNoSubstitutionTemplateLiteral(v.expression))) return v.expression.text;
  return "computed";
}

function buttonsIn(file: string, text: string): Button[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: Button[] = [];
  const visit = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && node.tagName.getText() === "button") {
      const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
      out.push({
        where: `${relative(SRC, file)}:${line}`,
        className: literal(node, "className"),
        variant: literal(node, "data-variant"),
        size: literal(node, "data-size"),
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

const dressed = (b: Button) =>
  typeof b.className === "string" &&
  b.className.split(/\s+/).includes("btn") &&
  typeof b.variant === "string" &&
  typeof b.size === "string";

describe("the house button, on the view plugins' surface", () => {
  it("reads the plugins' web halves and the marking control", () => {
    const files = new Set(SOURCES.map((f) => relative(SRC, f)));
    expect(files.has("../../view-plugins/chart/web/src/FacetGallery.tsx")).toBe(true);
    expect(files.has("../../view-plugins/chart/web/src/ChartView.tsx")).toBe(true);
    expect(files.has("renderers/entity/MarkingControl.tsx")).toBe(true);
    const all = SOURCES.flatMap((f) => buttonsIn(f, readFileSync(f, "utf8")));
    expect(all.length).toBeGreaterThan(5);
  });

  it("tells a bare, a computed and a dressed button apart", () => {
    const dir = mkdtempSync(join(tmpdir(), "button-class-"));
    const file = join(dir, "Edges.tsx");
    const src = [
      "export const A = () => <button>bare</button>;",
      'export const B = () => <button className={x ? "btn" : ""} data-variant="ghost" data-size="sm">computed</button>;',
      'export const C = () => <button className="btn" data-size="sm">no variant</button>;',
      'export const D = () => <button className="btn" data-variant="ghost" data-size="sm">dressed</button>;',
      'export const E = () => <button {...rest}>spread</button>;',
    ].join("\n");
    writeFileSync(file, src);
    expect(buttonsIn(file, src).map(dressed)).toEqual([false, false, false, true, false]);
  });

  it("dresses every button with .btn, a variant and a size", () => {
    const bare = SOURCES.flatMap((f) => buttonsIn(f, readFileSync(f, "utf8")))
      .filter((b) => !dressed(b))
      .map((b) => b.where);
    expect(bare, `${bare.length} button(s) without className="btn" data-variant data-size`).toEqual([]);
  });
});
