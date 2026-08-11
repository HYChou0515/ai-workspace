/**
 * Read a file under `web/src/` from a test, in either vitest environment.
 *
 * `new URL(..., import.meta.url)` only yields a file: URL in the `node`
 * environment; under `happy-dom` the module URL is an http: one and
 * `fileURLToPath` throws. A guard that reads the stylesheet has no business
 * caring which environment its neighbours picked, so the lookup falls back to
 * the vitest root (always `web/`).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = (() => {
  const here = import.meta.url;
  if (here.startsWith("file:")) return fileURLToPath(new URL(".", here));
  return resolve(process.cwd(), "src/test/");
})();

export function readSrcFile(relativeToSrc: string): string {
  return readFileSync(resolve(SRC, "..", relativeToSrc), "utf8");
}
