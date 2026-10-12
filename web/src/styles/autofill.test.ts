/**
 * Who the browser may fill. A password manager that holds a login for this
 * origin fills it into EVERY `type="password"` field it finds here, and takes
 * the nearest text field before each one for the user name: an env value, a
 * token, a variable's name, the Env panel's search box. Saving that login is
 * wanted (the sign-in dialog, user 2026-10-12: "以前的行為是對的"); filling it
 * anywhere else is the bug. `autoComplete="off"` does not stop it — Chrome
 * ignores it on a password field — so the rule is about the field's TYPE:
 *
 *   - `type="password"` exists only on a login's secret, which says so with
 *     `autoComplete="current-password"` (its user name says `"username"`).
 *     Every other secret is a text field masked by `data-masked` (`.input
 *     [data-masked="true"]` in base.css), which no password manager reads.
 *   - every text `<input>` names its `autoComplete` as a string literal, so
 *     what the browser may do there is decided where the field is written,
 *     not left to its guess.
 *
 * Read through the TypeScript parser like the `.input` guard beside it; a
 * spread on an `<input>` is refused, since an attribute it hides is one the
 * guard cannot check.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
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

const pluginSources = readdirSync(PLUGINS)
  .map((name) => join(PLUGINS, name, "web", "src"))
  .filter((dir) => {
    try {
      return statSync(dir).isDirectory();
    } catch {
      return false;
    }
  })
  .flatMap(walk);
const SOURCES = [...walk(SRC), ...pluginSources];

const NON_TEXT = new Set(["checkbox", "radio", "file", "range", "color", "hidden", "submit", "button", "image", "reset"]);
/** What a field may tell the browser: nothing to offer, or one half of a login. */
const ALLOWED = new Set(["off", "username", "current-password"]);

type Field = { where: string; type: string | null; mayBePassword: boolean; autoComplete: string | "computed" | null; spread: boolean };

function attr(el: ts.JsxOpeningLikeElement, name: string): ts.JsxAttribute | undefined {
  for (const a of el.attributes.properties) if (ts.isJsxAttribute(a) && a.name.getText() === name) return a;
  return undefined;
}

function literal(a: ts.JsxAttribute | undefined): string | "computed" | null {
  if (!a) return null;
  const v = a.initializer;
  if (v && ts.isStringLiteral(v)) return v.text;
  if (v && ts.isJsxExpression(v) && v.expression && (ts.isStringLiteral(v.expression) || ts.isNoSubstitutionTemplateLiteral(v.expression)))
    return v.expression.text;
  return "computed";
}

/** Whether any string literal inside the `type` expression is "password". */
function mentionsPassword(a: ts.JsxAttribute | undefined): boolean {
  let found = false;
  const visit = (n: ts.Node) => {
    if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && n.text === "password") found = true;
    ts.forEachChild(n, visit);
  };
  if (a?.initializer) visit(a.initializer);
  return found;
}

function fieldsOf(file: string): Field[] {
  const sf = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: Field[] = [];
  const visit = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && node.tagName.getText(sf) === "input") {
      const typeAttr = attr(node, "type");
      const type = literal(typeAttr);
      if (!(typeof type === "string" && type !== "computed" && NON_TEXT.has(type))) {
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
        out.push({
          where: `${relative(SRC, file)}:${line}`,
          type,
          mayBePassword: mentionsPassword(typeAttr),
          autoComplete: literal(attr(node, "autoComplete")),
          spread: node.attributes.properties.some((a) => ts.isJsxSpreadAttribute(a)),
        });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

const FIELDS = SOURCES.flatMap(fieldsOf);

describe("what the browser may fill", () => {
  it("reads a real number of fields", () => {
    // A walk that found nothing would pass every rule below.
    expect(FIELDS.length).toBeGreaterThan(50);
  });

  it("puts a password field only where a login's secret is typed", () => {
    const wrong = FIELDS.filter((f) => f.mayBePassword && f.autoComplete !== "current-password").map((f) => f.where);
    expect(wrong).toEqual([]);
  });

  it("says current-password only on a field that can be one", () => {
    const wrong = FIELDS.filter((f) => f.autoComplete === "current-password" && !f.mayBePassword).map((f) => f.where);
    expect(wrong).toEqual([]);
  });

  it("names autoComplete on every text field", () => {
    const wrong = FIELDS.filter((f) => f.spread || typeof f.autoComplete !== "string" || f.autoComplete === "computed" || !ALLOWED.has(f.autoComplete)).map(
      (f) => `${f.where} autoComplete=${f.spread ? "{...spread}" : String(f.autoComplete)}`,
    );
    expect(wrong).toEqual([]);
  });

  it("masks a secret with the stylesheet, not the password type", () => {
    const css = readFileSync(resolve(HERE, "base.css"), "utf8");
    expect(css).toMatch(/\.input\[data-masked="true"\]\s*\{[^}]*-webkit-text-security:\s*disc/);
  });
});
