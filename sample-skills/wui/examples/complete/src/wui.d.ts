/**
 * The `workspace` bridge, typed.
 *
 * This file is the reason a WUI is worth writing in TypeScript. The bridge is a
 * global injected by the renderer — there is no import to follow, so without
 * this there is no autocomplete and no checking — and two of its shapes are easy
 * to get wrong in a way that renders fine and is quietly incorrect, which is the
 * worst failure a WUI can have: the person looking at it cannot open a console.
 *
 * 1. `readFile` returns a UNION. `.text` does not exist on a binary file, so
 *    `JSON.parse(file.text)` against a `.png` is `JSON.parse(undefined)`. Typed,
 *    that is a compile error and you narrow on `kind` instead.
 * 2. `callTool` returns the tool's stdout as a STRING plus an exit code. Nothing
 *    here promises JSON — see SKILL.md, "Run the tool before you parse it".
 *
 * Copy this file unchanged. It describes the platform, not your page.
 */

/** One entry from `listFiles`. `read_only` is snake_case: it comes from the API,
 *  and it is OPTIONAL there — test it for truthiness, do not call methods on it. */
export interface WuiFile {
  path: string;
  size: number;
  read_only?: boolean;
}

/** A text file. `kind` is the discriminant; narrow on it before reading `text`. */
export interface WuiTextFile {
  path: string;
  kind: "text";
  text: string;
}

/** A binary file — an image, a PDF. Renderable as a `src`, not parseable. */
export interface WuiBinaryFile {
  path: string;
  kind: "binary";
  dataUrl: string;
}

export type WuiReadResult = WuiTextFile | WuiBinaryFile;

/** What a tool actually returns: the bytes it printed, and whether it succeeded. */
export interface WuiToolResult {
  /**
   * The tool's stdout, verbatim. NOT necessarily JSON — that is the tool's
   * contract, not the platform's, and guessing it is how a page goes blank.
   */
  output: string;
  /**
   * Non-zero is the TOOL's own failure, not a platform error. `output` is then
   * usually a sentence meant for a person, so show it rather than parse it.
   */
  exit_code: number;
}

/** What a sub-agent answered (`callAgent`, and a `startAgent` job's result). */
export interface WuiAgentResult {
  output: string;
  failed: boolean;
}

/**
 * A started job (`startTool` / `startAgent`). Nothing calls you back: ask it.
 * - `result()` waits for the answer — the same object the matching `call…`
 *   returns — and REJECTS if the job failed or was stopped.
 * - `status()` asks once how it is going; poll it yourself to draw progress.
 * - `stop()` stops it. A job is also listed in the item's runs, and can be
 *   stopped there.
 */
export interface WuiJob<T> {
  id: string;
  result(): Promise<T>;
  status(): Promise<{ state: "running" | "done" | "failed" | "stopped"; output?: T; error?: string }>;
  stop(): Promise<{ state: "running" | "done" | "failed" | "stopped" }>;
}

export interface Workspace {
  /** Everything under `prefix`, or the whole item when it is omitted. */
  listFiles(prefix?: string): Promise<{ files: WuiFile[] }>;
  /**
   * Anywhere in the item. Rejects when the file is not there, and that
   * rejection is the normal first-run path rather than a fault.
   */
  readFile(path: string): Promise<WuiReadResult>;
  /** ONLY inside this page's own folder; anything else rejects. */
  writeFile(path: string, text: string): Promise<{ path: string }>;
  /** ONLY inside this page's own folder. */
  deleteFile(path: string): Promise<{ path: string }>;
  /** Hands the user the real file, in the workspace beside the page. */
  openFile(path: string): Promise<{ path: string }>;
  /**
   * Who is looking at the page.
   *
   * `null` is REAL: the platform is still resolving the signed-in user when a
   * page opens, and a page that asks straight away gets it. Declaring this
   * `string` is what a `.d.ts` must never do — the compiler would certify
   * `who.user.trim()` and the page would throw on a cold open, blank, in front
   * of somebody who cannot see why.
   */
  whoami(): Promise<{ user: string | null }>;
  /**
   * Ask the platform to show ITS OWN sign-in for this item — drawn outside
   * your page, so what the person types there never reaches you. Takes
   * nothing: a credential must never pass through a page.
   *
   * Resolves once the sign-in is SHOWN, not when the person finishes. Call it
   * when a tool answers that it is not authorised, then let them try again.
   * What they sign in with fills their own values once they SAVE it. A tool
   * gets their value unless the item has a shared value for that name and
   * keeps the shared one first (the default).
   */
  openLogin(): Promise<{ opened: true }>;
  /** The page's only reach outside the item. Declare the tool in `tools:` first.
   *  `args` is optional — a tool that takes none is called with just a name. */
  callTool(name: string, args?: Record<string, unknown>): Promise<WuiToolResult>;
  /**
   * The same tool, STARTED instead of awaited — for a tool that may run longer
   * than the minute `callTool` waits. Answers at once with a job; its
   * `result()` is exactly what `callTool` would have returned.
   */
  startTool(name: string, args?: Record<string, unknown>): Promise<WuiJob<WuiToolResult>>;
  /**
   * Ask one of the item's sub-agents (its `.agent/<name>/AGENT.md`) and wait for
   * its answer — text in, text out. Declare it in `agents:` first. Waits at most
   * a minute: past that the work is stopped and this rejects, saying to use
   * `startAgent`. `failed: true` means the sub-agent itself failed or wrote
   * nothing; `output` then says why, in a sentence you can show.
   */
  callAgent(name: string, input: string): Promise<WuiAgentResult>;
  /** The same sub-agent, STARTED instead of awaited — for work that takes a while. */
  startAgent(name: string, input: string): Promise<WuiJob<WuiAgentResult>>;
  /**
   * Start a run and watch it happen.
   *
   * For work that takes MINUTES — read a folder, ask an agent to judge it,
   * write a summary back. `callTool` answers once; this reports progress the
   * whole way, so a page can show something instead of freezing for two
   * minutes. Closing the page does not cancel it: the run finishes and writes
   * its result back.
   *
   * `onEvent` receives the platform's events verbatim. **Ignore the ones you do
   * not recognise** — the set grows, and your page will not be edited again.
   *
   * The promise resolves when the run's stream ENDS. Do not use the `done`
   * event for that — it fires at the end of every turn, so a workflow with
   * three agent steps sends three. A workflow that parks on `awaiting_human`
   * does not resolve at all while it waits.
   */
  startRun(
    workflow: string,
    input: Record<string, unknown>,
    onEvent: (event: unknown) => void,
  ): Promise<void>;
  /** Someone else — a colleague, or the agent — changed a file. Not a promise. */
  onFileChanged(handler: (path: string) => void): void;
}

declare global {
  interface Window {
    workspace: Workspace;
  }
}
