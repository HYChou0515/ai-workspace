/**
 * Wire types and ApiClient interface. The source of truth for these
 * shapes is docs/contract.md §1-§2. Keep in lock-step.
 */

import type { AgentEvent, CellEvent } from "../events";

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

export type FileContent =
  | { kind: "text"; path: string; size: number; text: string }
  | { kind: "binary"; path: string; size: number };

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

export interface ApiClient {
  listInvestigations(): Promise<Investigation[]>;
  getInvestigation(id: string): Promise<Investigation>;
  createInvestigation(input: InvestigationInput): Promise<Investigation>;

  getConversation(investigationId: string): Promise<Conversation | null>;

  listFiles(investigationId: string, prefix?: string): Promise<FileInfo[]>;
  readFile(investigationId: string, path: string): Promise<FileContent>;
  writeFile(
    investigationId: string,
    path: string,
    body: string,
  ): Promise<void>;

  streamAgentEvents(args: SendMessageArgs): AsyncGenerator<AgentEvent>;
  streamCellEvents(args: ExecuteCellArgs): AsyncGenerator<CellEvent>;
}

/* --------------------------- Helpers ---------------------------- */

/** Format specstar's raw resource_id (e.g. `inv-2026-0142`) for display. */
export function formatInvestigationId(resourceId: string): string {
  const prefixed = resourceId.startsWith("INC-") ? resourceId : `INC-${resourceId.replace(/^inv-/i, "")}`;
  return prefixed.toUpperCase();
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
