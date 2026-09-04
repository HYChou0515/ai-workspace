/**
 * Rebuilding a page that has a build step, and watching it happen.
 *
 * A page written with a bundler has two halves — the `src/` someone edits and
 * the `dist/` a viewer sees — and they go out of step the moment a rebuild is
 * forgotten: the page renders, unchanged, with nothing saying why. Everything
 * here exists so that the person looking at the page can fix that themselves,
 * and can SEE it happening: a build takes tens of seconds and fails often while
 * someone is iterating, so the output is the feature and a spinner is not.
 */

import { apiFetch, HttpError } from "../../api/http";
import { parseSseStream } from "../../api/sse";

export type BuildEvent =
  | { type: "output"; text: string }
  | { type: "done"; exit_code: number };

/** Run a build for one folder, yielding its output as it arrives.
 *
 * `signal` is not optional politeness: abandoning the generator stops the
 * client READING, which tells the server nothing at all — the build runs to
 * completion for a page nobody is watching, and the next open starts a second
 * one beside it. Aborting is what turns "I have left" into a disconnect. */
export type RunBuild = (folder: string, signal?: AbortSignal) => AsyncGenerator<BuildEvent>;

/** Bind the build route to one item. */
export function itemBuild(slug: string, itemId: string): RunBuild {
  return async function* (folder: string, signal?: AbortSignal) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/build`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder }),
        signal,
      },
    );
    if (!resp.ok || !resp.body) {
      // The server's own sentence where there is one — it names what was wrong
      // with the request, and it reaches a person through the same log the
      // build's output uses. `detail` is a string for our refusals and an array
      // for a validation failure; passing the array through would print
      // "[object Object]" where the explanation should be.
      const detail = await resp
        .json()
        .then((b: { detail?: unknown }) => (typeof b.detail === "string" ? b.detail : undefined))
        .catch(() => undefined);
      // `HttpError`, not a bare `Error`: the STATUS decides what the pane does
      // next. A 403 — a viewer who may read the item but not run things in it —
      // is permanent for that person, so rebuilding on open is switched off
      // rather than refused again on every open, forever.
      throw new HttpError(
        resp.status,
        detail ?? `The build could not be started (${resp.status}).`,
      );
    }
    yield* parseSseStream<BuildEvent>(resp.body);
  };
}

/**
 * A build tool's output, as a browser can show it.
 *
 * Every one of them colours its output, and none of that survives into a
 * `<div>`: the pane showed `[32m✓[39m built in 565ms` — the escape sequences as
 * literal text — which reads as a broken tool rather than a working one. `\r`
 * has the same problem from the other side: it redraws a line in a terminal and
 * does nothing here, so a progress bar arrived as one unreadable run-on line.
 *
 * Anchored on the ESC character, so text that merely LOOKS like an escape — a
 * compiler quoting source, which is the reason to read this log at all — is
 * left exactly as it is.
 */
export function cleanBuildOutput(text: string): string {
  return text
    // CSI (colour, cursor moves) and the OSC title sequences some tools emit.
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, "")
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, "")
    .replace(/\r\n?/g, "\n");
}

/**
 * Does this folder's `package.json` declare the script the platform runs?
 *
 * `scripts.build` is exactly the question, because `pnpm run build` is exactly
 * what the route runs. "Is there a package.json" would put a Rebuild button in
 * front of a build that cannot run — and a folder may hold a manifest for other
 * reasons entirely.
 *
 * Anything unparseable answers NO rather than throwing: the file belongs to
 * whoever wrote the page, and taking the pane down over it would be a fault of
 * ours reported as a fault of theirs.
 */
export function hasBuildScript(packageJson: string): boolean {
  try {
    const parsed: unknown = JSON.parse(packageJson);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return false;
    const scripts = (parsed as { scripts?: unknown }).scripts;
    if (typeof scripts !== "object" || scripts === null) return false;
    const build = (scripts as { build?: unknown }).build;
    return typeof build === "string" && build.trim() !== "";
  } catch {
    return false;
  }
}
