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
  /** The page's only reach outside the item. Declare the tool in `tools:` first.
   *  `args` is optional — a tool that takes none is called with just a name. */
  callTool(name: string, args?: Record<string, unknown>): Promise<WuiToolResult>;
  /** Someone else — a colleague, or the agent — changed a file. Not a promise. */
  onFileChanged(handler: (path: string) => void): void;
}

declare global {
  interface Window {
    workspace: Workspace;
  }
}
