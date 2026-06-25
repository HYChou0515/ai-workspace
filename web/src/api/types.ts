import type { BodyEnhancements } from "../lib/kbEnhancementMode";

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

export type MessageRole = "user" | "assistant" | "tool" | "system" | "mention" | "error";

export type Message = {
  role: MessageRole;
  content: string;
  /** user id when role=user; agent name when role=assistant. */
  author?: string | null;
  /** LLM reasoning channel (Qwen3 <thinking>, OpenAI o-series). */
  reasoning?: string | null;
  tool_call_id?: string | null;
  tool_name?: string | null;
  /** role=tool only — the tool call's arguments (for log reload). */
  tool_args?: Record<string, unknown> | null;
  /** role=tool only (#62) — the FULL result (success stderr kept) when it
   * differs from the cleaned `content`. The card renders this when present so
   * an error that streamed live doesn't vanish on reload; absent ⇒ `content`. */
  tool_display?: string | null;
  /** Epoch ms the message was produced; restores the agent log's timestamps
   * after a reload. Absent for messages saved before this existed. */
  created_at?: number | null;
  /** Resolved [n] markers (KB answers). Rendered as clickable source cards. */
  citations?: MessageCitation[];
  /** role=mention only — the user ids summoned ("@ come look"). */
  mentions?: string[];
  /** role=error only (#37) — why the turn failed: error | cancelled |
   * max_turns. The thread renders the failure as a banner on reload. */
  error_kind?: string | null;
  /** #113 — "repetition" when role=assistant was stopped mid-stream for a
   * degenerate loop and the answer was truncated to before it. The view shows
   * a notice so a reloaded thread doesn't read the truncated answer as normal. */
  stopped_reason?: string | null;
};

/** A resolved [n] citation marker — points at a span of a source document. */
export type MessageCitation = {
  marker: number;
  collection_id: string;
  document_id: string;
  filename: string;
  start: number;
  end: number;
  source_chunk_ids: string[];
  snippet: string;
};

export type Conversation = {
  resource_id: string;
  investigation_id: string;
  messages: Message[];
};

// `read_only` (#205): files under the reserved `.readonly/` directory the IDE
// renders non-editable (the diff "before" snapshot). Optional for older backends.
export type FileInfo = { path: string; size: number; read_only?: boolean };

/** An agent profile the picker offers — model + prompt live BE-side; the
 * FE only needs enough to label the radio and PATCH the attachment.
 *
 * Identifier is `name`: post-refactor (commit 034fa96), the picker comes
 * from `runner.list_configs()` (Settings.agent_configs / config.yaml),
 * not from a specstar resource. `attached_agent_config_id` on the
 * Investigation now carries the config's `name` directly. */
/** One quick-prompt chip (#91). ``label`` is the button text, ``prompt`` is
 * what gets sent as the user message when the chip is pressed. */
export type Suggestion = {
  label: string;
  prompt: string;
};

/** One launcher card — a multi-app platform App (#89). `icon` is a named-icon
 * key, an emoji, or inline `<svg>` markup; `color` is the App's accent hex. */
export type AppSummary = {
  slug: string;
  title: string;
  description: string;
  icon: string;
  color: string;
};

/** A chip colour token — the design's canonical palette for enum values, mapped
 * to CSS vars by the chip components. App `field_styles` overlays pick from these. */
export type ChipTone = "err" | "warn" | "ok" | "info" | "muted";

/** One domain field's render schema (#89 P7b) — projected by the backend from
 * the App's model (a field with an enum → `select` + its values as `options`;
 * else `text`). The shell renders + inline-edits fields off this, never
 * restating types/options on the FE. Mirrors specstar autocrud's ResourceField. */
export type FieldSpec = {
  name: string;
  label: string;
  kind: "select" | "text" | "tags";
  options?: string[];
};

/** One read-only step/highlight in a welcome teaching (#161). */
export type OnboardingPoint = { title: string; body: string };

/** Versioned, read-only welcome teaching (#161) — used both per-App (from the
 * manifest) and platform-level (a FE constant). The modal pops until the user
 * dismisses *this* `version`; bumping `version` re-shows it. */
export type Onboarding = {
  version: string;
  title: string;
  intro: string;
  points: OnboardingPoint[];
};

/** The full App manifest (GET /apps/:slug) the dashboard + workspace drive off
 * (#89). `fields` carries each domain field's render kind + enum options (from
 * the model); `layout` + `labels` are the display overlay. */
export type AppManifest = AppSummary & {
  function: { workspace: boolean; sandbox: boolean; terminal: boolean };
  agent: {
    picker: { preset: string; name: string }[];
    suggestions?: Suggestion[];
    /** Topic Hub §6 — workspace files whose live content is injected each turn
     * (e.g. `MEMORY.md`, `collections.json`). The workspace derives whether to
     * show the collection-set picker from this containing `collections.json`
     * (#200), so there's no separate flag. Empty/absent for most Apps. */
    context_files?: string[];
  };
  item: { noun: string; noun_plural: string; create_label?: string };
  layout: {
    breadcrumb: string[];
    statusbar: string[];
    list: string[];
    form?: string[];
    /** Files the workspace opens on entry (filtered to those that exist). */
    default_tabs: string[];
    /** #159: which surface leads when an item opens — "chat" (default) tucks the
     * file IDE behind a `Workspace` toggle; "ide" opens the VS Code workspace up
     * front. Ignored when `function.workspace` is false (no IDE to show). */
    primary_surface: "chat" | "ide";
    /** #200: how prominent the per-item multi-chat switcher is — "auto" (default)
     * hides it until a second chat exists (single-chat-leaning); "always" surfaces
     * it up front (Topic Hub). Every App is multichat-capable regardless. */
    chat_switcher: "auto" | "always";
  };
  labels: Record<string, string>;
  fields: FieldSpec[];
  /** Display overlay: enum field → {option → tone}, so an App's chip palette is
   * data. The `select` renderer styles its chip from this; absent → neutral. */
  field_styles?: Record<string, Record<string, ChipTone>>;
  /** Close/resolve workflow; absent → the shell shows no Close affordance. */
  lifecycle?: { status_field: string; closing_states: string[] };
  default_profile: string;
  /** Versioned, read-only welcome teaching shown when entering the App (#161).
   * Absent → no per-App welcome. */
  onboarding?: Onboarding;
  /** The App's profiles (starter-content bundles) — the create flow offers a
   * picker when there's more than one. */
  profiles: { name: string; title: string; description: string }[];
  /** specstar CRUD route for this App's items, e.g. "/rca-investigation". */
  resource_route: string;
};

/** One row in an App's item list — the WorkItem fields plus its resource id.
 * App-declared (Tier 3) fields are extra keys read dynamically via `layout`.
 * `created_time` / `created_by` come from specstar's revision metadata (always
 * present); the FE surfaces them directly rather than falling back to `data`. */
export type AppItem = {
  resource_id: string;
  title: string;
  owner: string;
  created_time: string;
  created_by: string;
  updated_time?: string;
  [field: string]: unknown;
};

/** Lean subset of the specstar list/count query params the dashboard uses
 * (#95 follow-up). `data_conditions` is a JSON array of
 * `{ field_path, operator, value }`; arrays (created_bys/updated_bys) serialize
 * as repeated query params. Kept here (not imported from the excluded autocrud
 * codegen) so our app stays decoupled from it. */
export interface SearchParams {
  data_conditions?: string;
  created_bys?: string[];
  updated_bys?: string[];
  created_time_start?: string;
  created_time_end?: string;
  updated_time_start?: string;
  updated_time_end?: string;
  sorts?: string;
  limit?: number;
  offset?: number;
}

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

/** Per-message reasoning effort (the UI selector); omitted → model default. */
export type ReasoningEffort = "low" | "medium" | "high";

export type SendMessageArgs = {
  slug: string;
  investigationId: string;
  content: string;
  signal?: AbortSignal;
  reasoningEffort?: ReasoningEffort;
  /** Knowledge-search depth for this turn's ask_knowledge_base lookups
   * (composer picker); shape mirrors the KB chat body. */
  enhancements?: BodyEnhancements;
};

export type ExecuteCellArgs = {
  slug: string;
  investigationId: string;
  notebookPath: string;
  cellIndex: number;
  code: string;
  signal?: AbortSignal;
};

export type CloseStatus = "resolved" | "abandoned";

export type CellRef = {
  slug: string;
  investigationId: string;
  notebookPath: string;
  cellIndex: number;
};

export type NotebookRef = {
  slug: string;
  investigationId: string;
  notebookPath: string;
};

export type ExecResult = {
  exit_code: number;
  stdout: string;
  stderr: string;
};

export type User = {
  id: string;
  name: string;
  section: string;
  email: string;
  photo_url: string | null;
};

export type NotificationItem = {
  resource_id: string;
  kind: string; // mention | share | status | …
  title: string;
  body: string;
  link: string;
  actor: string | null;
  read: boolean;
  created_at: number | null;
};

export interface ApiClient {
  /** Id of the signed-in user (`GET /me`). The whole FE reads identity through
   * this; real auth swaps only the backend resolution. */
  getCurrentUser(): Promise<string>;
  /** GET /users — the company directory (small; fetch once, filter on the FE). */
  getUsers(): Promise<User[]>;
  /** GET /notifications — the current user's notifications, newest first. */
  getNotifications(): Promise<NotificationItem[]>;
  markAllNotificationsRead(): Promise<void>;
  markNotificationRead(id: string): Promise<void>;
  /** Close the workspace via the per-App route POST /a/{slug}/items/{id}/close.
   * `status` (one of the manifest's closing_states) flips the status; `null`
   * is a pure close — tear the session down, leave status alone. */
  closeInvestigation(slug: string, id: string, status: CloseStatus | null): Promise<void>;
  /** @mention users in an item — a "come look" summon that notifies them
   * (does NOT run the agent). POST /a/{slug}/items/{id}/mentions. */
  addMention(slug: string, investigationId: string, userIds: string[], note?: string): Promise<void>;
  /** GET /apps — launcher card summaries, one per registered App (#89). */
  listApps(): Promise<AppSummary[]>;
  /** GET /apps/{slug} — the full manifest the dashboard/workspace drive off. */
  getAppManifest(slug: string): Promise<AppManifest>;
  /** GET {resource_route} — the App's items (specstar list → flat rows).
   * Optional SearchParams filter/sort server-side (dashboard nav + filters). */
  listAppItems(resourceRoute: string, params?: SearchParams): Promise<AppItem[]>;
  /** GET {resource_route}/count — server-side count under the same filter
   * (dashboard nav badges). */
  countAppItems(resourceRoute: string, params?: SearchParams): Promise<number>;
  /** GET {resource_route}/{id} — one App item (specstar entry → flat row). */
  getAppItem(resourceRoute: string, id: string): Promise<AppItem>;
  /** POST /a/{slug}/items — create an item (+ seed its profile); returns the id. */
  createAppItem(slug: string, body: Record<string, unknown>): Promise<{ resource_id: string }>;
  /** PUT {resource_route}/{id} — replace an item (specstar CRUD update). Inline
   * field edits read the item, change one field, and PUT the whole. */
  updateAppItem(
    resourceRoute: string,
    id: string,
    data: Record<string, unknown>,
  ): Promise<{ resource_id: string }>;
  /** GET /activity — recent-activity feed (newest first). */
  listActivity(): Promise<ActivityEntry[]>;

  getConversation(investigationId: string): Promise<Conversation | null>;

  listFiles(slug: string, investigationId: string, prefix?: string): Promise<FileInfo[]>;
  /** POST /a/{slug}/items/{id}/files/refresh — force-mirror the live sandbox
   * to the snapshot (don't wait for the throttled sweep). Call this before a
   * read whenever the sandbox may have changed out-of-band (terminal `rm`,
   * an exec side-effect, slow flush). */
  refreshFiles(slug: string, investigationId: string): Promise<void>;
  readFile(slug: string, investigationId: string, path: string): Promise<FileContent>;
  /** Raw write. `body` may be a string (UTF-8) or a binary Blob/ArrayBuffer
   * — the FE uploads notebook JSON as string, attachments as Blob. */
  writeFile(slug: string, 
    investigationId: string,
    path: string,
    body: string | Blob | ArrayBuffer,
  ): Promise<void>;
  /** POST /a/{slug}/items/{id}/files/mkdir — create an empty folder (real
   * directory; no .keep placeholder). 409 if a file occupies the path. */
  mkdir(slug: string, investigationId: string, path: string): Promise<void>;
  /** GET /a/{slug}/items/{id}/dirs — directory paths incl. empty ones. */
  listDirs(slug: string, investigationId: string): Promise<string[]>;
  /** DELETE /a/{slug}/items/{id}/files/{path} → 204. Removes a file, or a
   * folder and its whole subtree when the path is a directory. */
  deleteFile(slug: string, investigationId: string, path: string): Promise<void>;
  /** POST /a/{slug}/items/{id}/files/move — rename/move (409 if target exists). */
  moveFile(slug: string, investigationId: string, from: string, to: string): Promise<void>;
  /** POST /a/{slug}/items/{id}/files/copy — duplicate (409 if target exists). */
  copyFile(slug: string, investigationId: string, from: string, to: string): Promise<void>;

  /** POST /a/{slug}/items/{id}/messages — #43: enqueues the turn (202) and
   * resolves once it's accepted; it NO LONGER streams. The turn's events arrive
   * via `subscribeInvestigation`, the shared per-investigation broadcast. */
  sendMessage(args: SendMessageArgs): Promise<void>;
  /** GET /a/{slug}/items/{id}/stream — #43: the long-lived broadcast every
   * viewer subscribes to. Yields ALL turns live (whoever sent them) plus the
   * broadcast-only `user_message` / `file_changed` events. */
  subscribeInvestigation(slug: string, investigationId: string, signal?: AbortSignal): AsyncGenerator<AgentEvent>;
  /** DELETE /a/{slug}/items/{id}/messages/current — tears the in-flight
   * agent turn down on the BE so the kernel/sandbox stop spending tokens.
   * Idempotent: safe to call when nothing's running. */
  cancelMessage(slug: string, investigationId: string): Promise<void>;
  /** DELETE /a/{slug}/items/{id}/messages?turns=N — undo the last N whole
   * turns (#38). Removes the prompt + its agent response as a unit. Does
   * NOT revert workspace files. Returns the conversation's new length. */
  undoTurns(slug: string, investigationId: string, turns: number): Promise<{ message_count: number }>;

  streamCellEvents(args: ExecuteCellArgs): AsyncGenerator<CellEvent>;
  /** DELETE /a/{slug}/items/{id}/notebooks/{path}/cells/{idx}/execute —
   * stops the cell on the kernel side. Idempotent. */
  interruptCell(ref: CellRef): Promise<void>;
  /** POST /a/{slug}/items/{id}/notebooks/{path}/kernel/restart — wipes
   * the kernel's namespace; next execute spawns a fresh kernel. */
  restartKernel(ref: NotebookRef): Promise<void>;

  /** POST /a/{slug}/items/{id}/exec — run a shell command in the
   * sandbox and return its ExecResult. Backs the Terminal pane. `signal`
   * lets the terminal's Stop button abort a long-running command. */
  execShell(slug: string, investigationId: string, cmd: string[], signal?: AbortSignal): Promise<ExecResult>;

  /** POST /a/{slug}/items/{id}/search — global text search over the
   * FileStore. Empty query → no results. */
  searchFiles(slug: string, 
    investigationId: string,
    query: string,
    opts?: SearchOptions,
  ): Promise<SearchResult[]>;
  /** POST /a/{slug}/items/{id}/replace — replace every match across the
   * (filtered) files; returns the total replacement count. */
  replaceInFiles(slug: string, 
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
