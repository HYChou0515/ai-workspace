/**
 * Wire types and ApiClient interface. The source of truth for these
 * shapes is docs/contract.md §1-§2. Keep in lock-step.
 */

import type { AgentEvent, CellEvent } from "../events";
import type { FileEncoding } from "./encoding";

export type { FileEncoding };

/* ----------------------------- Enums ----------------------------- */

export type Severity = "P0" | "P1" | "P2" | "P3" | "P4";

export type Status =
  | "triaging"
  | "awaiting_review"
  | "resolved"
  | "abandoned";

/* ------------------------- Domain models ------------------------- */

/**
 * Investigation as seen on the wire (specstar `data` + auto fields).
 * Display-only derived fields (id format, summary, agent state, pinned)
 * are computed FE-side — see contract.md §1.4 and helpers in this file.
 */
export type Investigation = {
  resource_id: string;
  title: string;
  owner: string;
  description: string;
  severity: Severity;
  status: Status;
  product: string;
  members: string[];
  topics: string[];
  attached_agent_config_id: string | null;
  created_time: string;
  updated_time: string;
};

export type InvestigationInput = {
  title: string;
  description?: string;
  severity?: Severity;
  product?: string;
  topics?: string[];
  /** Template profile to seed from (default "default"). */
  templateProfile?: string;
};

export type MessageRole = "user" | "assistant" | "tool" | "system";

export type Message = {
  role: MessageRole;
  content: string;
  /** user id when role=user; agent name when role=assistant. */
  author?: string | null;
  /** LLM reasoning channel (Qwen3 <thinking>, OpenAI o-series). */
  reasoning?: string | null;
  tool_call_id?: string | null;
  tool_name?: string | null;
};

export type Conversation = {
  resource_id: string;
  investigation_id: string;
  messages: Message[];
};

export type FileInfo = { path: string; size: number };

/** An agent profile the picker offers — model + prompt live BE-side; the
 * FE only needs enough to label the radio and PATCH the attachment. */
export type AgentConfigInfo = {
  resource_id: string;
  name: string;
  model: string;
  /** Quick-prompt chips for the agent panel (sent verbatim when clicked). */
  suggestions: string[];
};

export type ActivityEntry = {
  ts: string; // ISO-8601
  kind: string; // investigation_created | file_written | agent_turn_complete | ...
  text: string;
  ref: { investigation_id?: string; path?: string };
};

export type FileContent =
  | { kind: "text"; path: string; size: number; text: string; encoding: FileEncoding }
  | { kind: "binary"; path: string; size: number };

/** VSCode-style search toggles (#8). */
export type SearchOptions = {
  regex?: boolean;
  caseSensitive?: boolean;
  wholeWord?: boolean;
  /** comma/space-separated globs to include (empty = all). */
  include?: string;
  /** comma/space-separated globs to exclude. */
  exclude?: string;
};

export type SearchMatch = { line: number; col: number; text: string };
export type SearchResult = { path: string; matches: SearchMatch[] };

/* ---------------------- ApiClient interface ---------------------- */

export type SendMessageArgs = {
  investigationId: string;
  content: string;
  signal?: AbortSignal;
};

export type ExecuteCellArgs = {
  investigationId: string;
  notebookPath: string;
  cellIndex: number;
  code: string;
  signal?: AbortSignal;
};

export type CloseStatus = "resolved" | "abandoned";

export type CellRef = {
  investigationId: string;
  notebookPath: string;
  cellIndex: number;
};

export type NotebookRef = {
  investigationId: string;
  notebookPath: string;
};

export type ExecResult = {
  exit_code: number;
  stdout: string;
  stderr: string;
};

export interface ApiClient {
  listInvestigations(): Promise<Investigation[]>;
  getInvestigation(id: string): Promise<Investigation>;
  createInvestigation(input: InvestigationInput): Promise<Investigation>;
  /** Close the workspace. `status` resolved|abandoned flips the status;
   * `null` is a pure close — tear the session down, leave status alone. */
  closeInvestigation(id: string, status: CloseStatus | null): Promise<void>;
  /** GET /agent-config — agent profiles the picker offers. */
  listAgentConfigs(): Promise<AgentConfigInfo[]>;
  /** PATCH /investigation/{id} — attach (or, with null, detach) the agent
   * config that drives this investigation's turns. */
  attachAgentConfig(investigationId: string, configId: string | null): Promise<void>;
  /** GET /templates — template profile names for the New Investigation picker. */
  listTemplates(): Promise<string[]>;
  /** GET /activity — recent-activity feed (newest first). */
  listActivity(): Promise<ActivityEntry[]>;

  getConversation(investigationId: string): Promise<Conversation | null>;

  listFiles(investigationId: string, prefix?: string): Promise<FileInfo[]>;
  readFile(investigationId: string, path: string): Promise<FileContent>;
  /** Raw write. `body` may be a string (UTF-8) or a binary Blob/ArrayBuffer
   * — the FE uploads notebook JSON as string, attachments as Blob. */
  writeFile(
    investigationId: string,
    path: string,
    body: string | Blob | ArrayBuffer,
  ): Promise<void>;
  /** POST /investigations/{id}/files/mkdir — create an empty folder (real
   * directory; no .keep placeholder). 409 if a file occupies the path. */
  mkdir(investigationId: string, path: string): Promise<void>;
  /** GET /investigations/{id}/dirs — directory paths incl. empty ones. */
  listDirs(investigationId: string): Promise<string[]>;
  /** DELETE /investigations/{id}/files/{path} → 204. Removes a file, or a
   * folder and its whole subtree when the path is a directory. */
  deleteFile(investigationId: string, path: string): Promise<void>;
  /** POST /investigations/{id}/files/move — rename/move (409 if target exists). */
  moveFile(investigationId: string, from: string, to: string): Promise<void>;
  /** POST /investigations/{id}/files/copy — duplicate (409 if target exists). */
  copyFile(investigationId: string, from: string, to: string): Promise<void>;

  streamAgentEvents(args: SendMessageArgs): AsyncGenerator<AgentEvent>;
  /** DELETE /investigations/{id}/messages/current — tears the in-flight
   * agent turn down on the BE so the kernel/sandbox stop spending tokens.
   * Idempotent: safe to call when nothing's running. */
  cancelMessage(investigationId: string): Promise<void>;

  streamCellEvents(args: ExecuteCellArgs): AsyncGenerator<CellEvent>;
  /** DELETE /investigations/{id}/notebooks/{path}/cells/{idx}/execute —
   * stops the cell on the kernel side. Idempotent. */
  interruptCell(ref: CellRef): Promise<void>;
  /** POST /investigations/{id}/notebooks/{path}/kernel/restart — wipes
   * the kernel's namespace; next execute spawns a fresh kernel. */
  restartKernel(ref: NotebookRef): Promise<void>;

  /** POST /investigations/{id}/exec — run a shell command in the
   * sandbox and return its ExecResult. Backs the Terminal pane. */
  execShell(investigationId: string, cmd: string[]): Promise<ExecResult>;

  /** POST /investigations/{id}/search — global text search over the
   * FileStore. Empty query → no results. */
  searchFiles(
    investigationId: string,
    query: string,
    opts?: SearchOptions,
  ): Promise<SearchResult[]>;
  /** POST /investigations/{id}/replace — replace every match across the
   * (filtered) files; returns the total replacement count. */
  replaceInFiles(
    investigationId: string,
    query: string,
    replacement: string,
    opts?: SearchOptions,
  ): Promise<number>;
}

/* --------------------------- Helpers ---------------------------- */

/** Short display form of a resource_id — the first 8 hex of the uuid,
 * no `INC-` prefix. `investigation:96863dd1-...` → `96863dd1`.
 */
export function formatInvestigationId(resourceId: string): string {
  const tail = (resourceId.split(":").pop() ?? resourceId).replace(/-/g, "");
  return tail.slice(0, 8) || resourceId;
}

/** First non-empty line of `description`. */
export function summarize(description: string): string {
  for (const line of description.split("\n")) {
    const t = line.trim();
    if (t.length > 0) return t;
  }
  return "";
}

/** Coarse-grained relative time. Good enough for table cells. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, now.getTime() - then);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "just now";
  if (diff < hour) return `${Math.floor(diff / minute)} min ago`;
  if (diff < day) return `${Math.floor(diff / hour)} h ago`;
  return `${Math.floor(diff / day)} d ago`;
}

export function isCritical(sev: Severity): boolean {
  return sev === "P0" || sev === "P1";
}

export function isOpen(status: Status): boolean {
  return status === "triaging" || status === "awaiting_review";
}
