/**
 * Nothing under `web/src` may import from outside `web/`.
 *
 * The web image is built from `COPY web/ ./` alone (`docker/Dockerfile`) —
 * `sample-skills/`, `src/`, `docs/` are copied later, into the PYTHON stage.
 * So a relative import that climbs past the package root resolves fine on a
 * full checkout, passes `frontend-test` in CI, and then fails the image build
 * with "Cannot find module", in a pipeline nobody was watching.
 *
 * That happened: a test importing the shipped WUI example reached
 * `../../../../sample-skills/...` from `src/renderers/wui/`. It is now in
 * `web/tests/`, which `tsconfig`'s `include: ["src"]` leaves out of the build's
 * typecheck while vitest still runs it.
 *
 * The rule is about `src` specifically, not about the whole package: `src` is
 * what the build compiles, and it is the half that must stand alone.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const WEB = resolve(fileURLToPath(new URL("..", import.meta.url)));
const SRC = join(WEB, "src");

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === "node_modules") continue;
      out.push(...sourceFiles(full));
    } else if (/\.(ts|tsx|js|jsx)$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

/** Every `from "…"` / `import("…")` specifier in a file, with its line. */
function specifiers(text: string): { spec: string; line: number }[] {
  const out: { spec: string; line: number }[] = [];
  text.split("\n").forEach((line, i) => {
    for (const m of line.matchAll(/(?:from|import)\s*\(?\s*["']([^"']+)["']/g)) {
      out.push({ spec: m[1], line: i + 1 });
    }
  });
  return out;
}

describe("the web package stands alone", () => {
  it("has source files to check", () => {
    // Without this the sweep below is satisfied by finding nothing at all —
    // the same failure mode as a guard whose extractor stopped matching.
    expect(sourceFiles(SRC).length).toBeGreaterThan(100);
  });

  it("never imports from outside web/", () => {
    const escapes: string[] = [];

    for (const file of sourceFiles(SRC)) {
      for (const { spec, line } of specifiers(readFileSync(file, "utf8"))) {
        if (!spec.startsWith(".")) continue; // a package, resolved from node_modules
        const target = resolve(join(file, ".."), spec);
        if (!target.startsWith(WEB)) {
          escapes.push(`${relative(WEB, file)}:${line} → ${spec}`);
        }
      }
    }

    expect(
      escapes,
      "these climb out of web/, which the image build never copies — they " +
        "resolve on a full checkout and fail `tsc --noEmit` inside the image",
    ).toEqual([]);
  });
});
