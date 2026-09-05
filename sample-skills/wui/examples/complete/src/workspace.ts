/**
 * Everything this page does with the platform, in one file.
 *
 * Split out from the UI on purpose: this half is the same for every WUI, and it
 * is where the mistakes that cannot be seen live. `main.tsx` below is just a
 * page. Copy this file nearly as-is and change the shapes.
 */

import type { WuiFile, WuiToolResult } from "./wui.d";

/** Every failure this page can show, already a sentence. */
export type Problem = { where: string; message: string };

/**
 * The platform's own sentence, not one of ours.
 *
 * A refusal from `workspace.*` names the thing the reader can change ("this
 * page can only write inside its own folder"). Replacing it with "could not
 * save" throws that away, and it is the message they forward to the agent.
 */
export function sentence(err: unknown): string {
  return err instanceof Error && err.message ? err.message : String(err);
}

/** A text file's contents, or `null` when it is not text (an image, a PDF). */
export function textOf(file: { kind: string; text?: string }): string | null {
  // `readFile` returns a UNION. `.text` exists on one arm only, so this is the
  // compiler making you handle the day somebody drops a PNG where the data was
  // — untyped, `JSON.parse(undefined)` throws and the catch below turns it into
  // an empty page over a file that is right there.
  return file.kind === "text" ? (file.text ?? null) : null;
}

/**
 * Read many files at once, keeping the ones that worked.
 *
 * `Promise.all` would lose 199 good records to one unreadable file. The failures
 * come back beside the results so the page can SHOW them — a page that silently
 * drops rows is a page that lies about the data.
 */
export async function readAll<T>(
  paths: string[],
  parse: (text: string, path: string) => T | null,
): Promise<{ rows: T[]; problems: Problem[] }> {
  const settled = await Promise.all(
    paths.map(async (path) => {
      try {
        const file = await window.workspace.readFile(path);
        const text = textOf(file);
        if (text === null) return { path, skip: `${path} is not a text file.` };
        return { path, row: parse(text, path) };
      } catch (err) {
        return { path, skip: sentence(err) };
      }
    }),
  );
  const rows: T[] = [];
  const problems: Problem[] = [];
  for (const s of settled) {
    if ("row" in s && s.row !== null && s.row !== undefined) {
      rows.push(s.row);
    } else if ("skip" in s && s.skip) {
      problems.push({ where: s.path, message: s.skip });
    } else {
      // `parse` returned null: the file was readable and its contents were not
      // what we expected. That is a PROBLEM, not a non-event — and without this
      // branch it was neither: not a row, and not carrying a `skip`, so it fell
      // out of both and vanished. A folder with one malformed file rendered a
      // short table and an empty problem list, which is the one outcome a reader
      // cannot tell apart from "there is less data than I thought".
      problems.push({ where: s.path, message: "Not in the shape this page expects." });
    }
  }
  return { rows, problems };
}

/** Files under a prefix, in whatever order the platform lists them — nothing
 * here sorts, so do not rely on one. Absence is an empty list. */
export async function list(prefix: string): Promise<WuiFile[]> {
  const { files } = await window.workspace.listFiles(prefix);
  return files;
}

/**
 * The path a tool answered with, made safe to read.
 *
 * THE MISTAKE THIS EXISTS TO PREVENT, seen in production:
 *
 * A tool with a large result does not print megabytes to stdout — it writes a
 * file and prints the PATH. And it names that file the way the WORKSPACE names
 * it: `scrap-review/out.json`, with no leading slash, because that is what a
 * workspace path looks like everywhere else.
 *
 * `readFile` reads a bare path as one NEXT TO THIS PAGE. So that string becomes
 * `/scrap-review/scrap-review/out.json` — the folder twice — and the read
 * fails. Before the platform learned to say so, it failed the same way a first
 * run does, and the page showed "nothing found" forever over a perfectly good
 * answer.
 *
 * So: anything that came from somewhere else gets a leading slash. A path this
 * page wrote itself does not.
 */
export function fromItemRoot(pathFromTool: string): string {
  const trimmed = pathFromTool.trim();
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

/** What a tool answered, once we have looked at it rather than assumed. */
export type ToolAnswer =
  | { kind: "json"; value: unknown }
  | { kind: "path"; path: string }
  | { kind: "text"; text: string }
  | { kind: "failed"; message: string };

/**
 * Call a tool and CLASSIFY what came back, instead of guessing.
 *
 * `callTool` hands back `{ output, exit_code }` where `output` is whatever bytes
 * the command printed. Whether that is JSON is the tool's contract and the
 * platform promises nothing about it — so run your tool once before you write
 * this function, and make the branches match what you actually saw.
 */
export async function callTool(name: string, args: Record<string, unknown>): Promise<ToolAnswer> {
  let res: WuiToolResult;
  try {
    res = await window.workspace.callTool(name, args);
  } catch (err) {
    // A REFUSAL, not the tool's failure. Three different causes, three different
    // people who can fix them, and the message says which — do not collapse
    // them into "it failed":
    //   "did not declare X"   → add it to `tools:` in page.ai.yaml (yours)
    //   "does not offer X"    → this app does not grant it (an operator's)
    //   "X is unavailable: …" → granted, but could not be resolved
    return { kind: "failed", message: sentence(err) };
  }

  // Non-zero is the TOOL saying no, not the platform failing. Its own output is
  // the explanation and it is the only one there is.
  if (res.exit_code !== 0) {
    return { kind: "failed", message: res.output.trim() || `${name} exited ${res.exit_code}.` };
  }

  const out = res.output.trim();
  if (!out) return { kind: "text", text: "" };

  if (out.startsWith("{") || out.startsWith("[")) {
    try {
      const value = JSON.parse(out);
      // A tool with a large result answers `{"path": "..."}` rather than the
      // data. Recognise that BEFORE treating the object as the answer: parsing
      // it succeeds, and a page that then looks for rows in it finds none and
      // says "nothing found" over a file it never opened.
      // ...and ONLY when the path is all it answered. A tool replying
      // `{"path": "/out.json", "rows": [...]}` has handed over the data AND
      // said where it lives; classifying that as a path throws the rows away
      // and sends the page off to read a file for something it already had.
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const obj = value as { path?: unknown };
        if (typeof obj.path === "string" && Object.keys(obj).length === 1) {
          return { kind: "path", path: obj.path };
        }
      }
      return { kind: "json", value };
    } catch {
      // It answered, just not in JSON. Show it rather than blanking the page.
      return { kind: "text", text: out };
    }
  }

  // Some tools print a bare path and nothing else.
  if (!out.includes("\n") && /\.(json|csv|txt|ndjson)$/i.test(out)) return { kind: "path", path: out };

  return { kind: "text", text: out };
}

/** Save this page's own data. ONLY paths inside this folder are allowed. */
export async function save(path: string, value: unknown): Promise<void> {
  await window.workspace.writeFile(path, JSON.stringify(value, null, 2));
}

/** What a page shows while a run is happening. */
export type RunProgress = { note: string; done: boolean; failed: boolean };

/**
 * Turn the platform's events into something a page can draw.
 *
 * ⚠️ **IT IGNORES WHAT IT DOES NOT RECOGNISE, AND THAT IS THE POINT.** This
 * function is copied into your page and will never be edited again; the
 * platform's event set, meanwhile, grows. A reducer that switches on every known
 * type and throws on the rest breaks the day a new one appears — in a page whose
 * author has long since moved on. One that shrugs keeps working forever.
 *
 * So: recognise what you need, return the previous state for everything else,
 * and never assume the shape of a field you have not checked.
 */
export function reduceRunEvent(prev: RunProgress, event: unknown): RunProgress {
  if (!event || typeof event !== "object") return prev;
  const e = event as { type?: unknown; text?: unknown; message?: unknown; name?: unknown };
  if (e.type === "error") {
    // `message`, which is the field `RunError` carries. Reading `text` here
    // always missed and fell through to our own wording — throwing away the one
    // sentence that says what actually went wrong.
    return { note: typeof e.message === "string" ? e.message : "It failed.", done: true, failed: true };
  }
  if (e.type === "awaiting_human") {
    // An end state of its own: the run is parked until somebody decides, and
    // the promise will not settle while it waits.
    return { ...prev, note: "Waiting for a decision.", done: true };
  }
  // NOT `done`. That event fires at the end of every TURN, so a workflow with
  // three agent steps sends three of them — a page that treats the first as the
  // finish re-enables its button while the run is still going. The run is over
  // when the stream ends, which is when `startRun`'s promise resolves.
  if (e.type === "step_started" && typeof e.name === "string") {
    return { ...prev, note: e.name };
  }
  if (typeof e.text === "string" && e.text.trim()) {
    return { ...prev, note: e.text.trim() };
  }
  return prev; // something new. Not our business.
}

/** Who is looking. `null` while the platform is still resolving them. */
export async function whoami(): Promise<string> {
  const who = await window.workspace.whoami();
  return who.user ?? "";
}
