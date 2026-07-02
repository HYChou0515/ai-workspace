/**
 * In-memory mock backend. Selected via `VITE_USE_MOCK=1`. Lifetime =
 * page session — refresh wipes state. Wire shapes follow contract.md.
 *
 * Seed data is realistic enough to populate the Home table and exercise
 * filter / count logic; the streaming scripts mirror the variants in
 * AgentEvent so the agent panel can be developed offline.
 */

import type { AgentEvent, CellEvent } from "../events";
import { isReadOnlyPath } from "../lib/readonly";
import type {
  ApiClient,
  AppItem,
  AppManifest,
  CellRef,
  CloseStatus,
  Conversation,
  ExecuteCellArgs,
  FileContent,
  Message,
  NotebookRef,
  SearchMatch,
  SearchOptions,
  SearchResult,
  SendMessageArgs,
} from "./types";

/* --- search core: mirrors the BE's api/search.py for offline parity --- */

function compileQuery(query: string, opts: SearchOptions): RegExp {
  let pattern = opts.regex ? query : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (opts.wholeWord) pattern = `\\b(?:${pattern})\\b`;
  return new RegExp(pattern, opts.caseSensitive ? "g" : "gi");
}

function globs(spec: string | undefined): string[] {
  return (spec ?? "").split(/[,\s]+/).filter(Boolean);
}

function globToRe(glob: string): RegExp {
  const g = glob.replace(/^\//, "");
  // "dir/**" → anything under dir/
  if (g.endsWith("/**")) {
    const prefix = g.slice(0, -3).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`^${prefix}(?:/.*)?$`);
  }
  const re = g
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, "[^/]*")
    .replace(/\?/g, "[^/]");
  return new RegExp(`^${re}$`);
}

function pathSelected(path: string, include: string | undefined, exclude: string | undefined): boolean {
  const rel = path.replace(/^\//, "");
  const inc = globs(include).map(globToRe);
  const exc = globs(exclude).map(globToRe);
  if (inc.length && !inc.some((re) => re.test(rel))) return false;
  if (exc.length && exc.some((re) => re.test(rel))) return false;
  return true;
}

function searchLines(text: string, re: RegExp): SearchMatch[] {
  const out: SearchMatch[] = [];
  text.split("\n").forEach((line, i) => {
    re.lastIndex = 0;
    const m = re.exec(line);
    if (m) out.push({ line: i + 1, col: m.index + 1, text: line.slice(0, 400) });
  });
  return out;
}

type MockFile = { text: string | null; bytes: number };

function nowIso(offsetMin = 0): string {
  return new Date(Date.now() - offsetMin * 60_000).toISOString();
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// #89: the mock dashboard's in-memory items (createAppItem appends here;
// listAppItems / getAppItem / closeInvestigation read it). Seeded with demo
// rows so a fresh mock dashboard isn't empty. Typed AppItem — the extra
// domain fields (severity/status/product/…) ride the index signature.
const _mockAppItemSeed = [
  {
    resource_id: "INC-2026-0142",
    title: "Reflow zone-3 drift on MX-7 board",
    owner: "alice",
    description:
      "AOI flagged a step-up in solder bridging on lot 25-W14.\n\nFirst seen 08-14 13:30. Correlated to set-point drop on reflow zone-3 (peak temp -8C vs spec).",
    severity: "P1",
    status: "triaging",
    product: "MX-7 board",
    members: ["bob", "carla"],
    topics: ["SMT 1", "Reflow zone-3", "MX-7"],
    attached_agent_config_id: null,
    created_time: nowIso(8 * 60),
    updated_time: nowIso(12),
  },
  {
    resource_id: "INC-2026-0141",
    title: "Solder ball BOM mismatch on KX-2 line",
    owner: "bob",
    description: "Stencil revision misaligned with current paste viscosity.",
    severity: "P2",
    status: "awaiting_review",
    product: "KX-2 board",
    members: [],
    topics: ["SMT 2", "Stencil"],
    attached_agent_config_id: null,
    created_time: nowIso(48 * 60),
    updated_time: nowIso(96),
  },
  {
    resource_id: "INC-2026-0140",
    title: "X-ray void rate exceeds 7% on BGA-9",
    owner: "carla",
    description: "Suspect underfill cure profile.\nNew bake step planned.",
    severity: "P0",
    status: "triaging",
    product: "BGA-9",
    members: ["alice", "dave"],
    topics: ["X-ray void", "BGA-9"],
    attached_agent_config_id: null,
    created_time: nowIso(60),
    updated_time: nowIso(2),
  },
  {
    resource_id: "INC-2026-0139",
    title: "AOI false positives spike — camera 4",
    owner: "dave",
    description: "Lens dust suspected; replaced filter, monitoring.",
    severity: "P3",
    status: "triaging",
    product: "AOI station 4",
    members: [],
    topics: ["AOI scan"],
    attached_agent_config_id: null,
    created_time: nowIso(7 * 60),
    updated_time: nowIso(45),
  },
  {
    resource_id: "INC-2026-0138",
    title: "OQC fail — connector recessed on cable assy",
    owner: "alice",
    description: "Two units in lot 25-W13 had connector pin recess > spec.",
    severity: "P2",
    status: "awaiting_review",
    product: "Cable assy",
    members: ["bob"],
    topics: ["OQC fail", "Cable"],
    attached_agent_config_id: null,
    created_time: nowIso(72 * 60),
    updated_time: nowIso(140),
  },
  {
    resource_id: "INC-2026-0137",
    title: "Tape-and-reel jam on placer 3",
    owner: "bob",
    description: "Resolved by replacing feeder spring. No defects shipped.",
    severity: "P3",
    status: "resolved",
    product: "Placer 3",
    members: [],
    topics: ["SMT 1", "Feeder"],
    attached_agent_config_id: null,
    created_time: nowIso(96 * 60),
    updated_time: nowIso(20 * 60),
  },
  {
    resource_id: "INC-2026-0136",
    title: "Lab humidity excursion — paste shelf life",
    owner: "carla",
    description: "Closed without RC after dehumidifier serviced.",
    severity: "P4",
    status: "abandoned",
    product: "Lab",
    members: [],
    topics: ["Environment"],
    attached_agent_config_id: null,
    created_time: nowIso(168 * 60),
    updated_time: nowIso(150 * 60),
  },
  {
    resource_id: "INC-2026-0135",
    title: "Insufficient solder on QFN-48 corner pads",
    owner: "alice",
    description: "Paste deposition volume below target on stencil aperture A4.",
    severity: "P1",
    status: "triaging",
    product: "QFN-48",
    members: ["dave"],
    topics: ["Stencil", "QFN"],
    attached_agent_config_id: null,
    created_time: nowIso(5 * 60),
    updated_time: nowIso(30),
  },
  {
    resource_id: "INC-2026-0134",
    title: "Flux residue under shield on RF module",
    owner: "dave",
    description: "Visual inspection flagged; cleaning step missing on rev-C.",
    severity: "P2",
    status: "triaging",
    product: "RF module",
    members: [],
    topics: ["Cleaning", "RF"],
    attached_agent_config_id: null,
    created_time: nowIso(10 * 60),
    updated_time: nowIso(80),
  },
  {
    resource_id: "INC-2026-0133",
    title: "Conformal coat coverage gaps on MX-7",
    owner: "bob",
    description: "Edge connector pins exposed on 3/120 units.",
    severity: "P2",
    status: "triaging",
    product: "MX-7 board",
    members: ["alice"],
    topics: ["Conformal coat", "MX-7"],
    attached_agent_config_id: null,
    created_time: nowIso(20 * 60),
    updated_time: nowIso(180),
  },
  {
    resource_id: "INC-2026-0132",
    title: "Wave solder bridging on USB-C header",
    owner: "alice",
    description: "Pitch may be too tight for current wave preheat.",
    severity: "P1",
    status: "triaging",
    product: "USB-C header",
    members: [],
    topics: ["Wave solder"],
    attached_agent_config_id: null,
    created_time: nowIso(4 * 60),
    updated_time: nowIso(15),
  },
  {
    resource_id: "INC-2026-0131",
    title: "Pick-and-place misalignment on 0402 caps",
    owner: "carla",
    description: "Vision system calibration overdue.",
    severity: "P3",
    status: "awaiting_review",
    product: "Placer 2",
    members: [],
    topics: ["SMT 2", "Calibration"],
    attached_agent_config_id: null,
    created_time: nowIso(30 * 60),
    updated_time: nowIso(8 * 60),
  },
  {
    resource_id: "INC-2026-0130",
    title: "Cold solder joints on 24V power rail",
    owner: "dave",
    description: "Thermocouple reading vs. profile shows preheat short.",
    severity: "P2",
    status: "triaging",
    product: "Power board",
    members: ["alice"],
    topics: ["Reflow zone-3", "Power"],
    attached_agent_config_id: null,
    created_time: nowIso(6 * 60),
    updated_time: nowIso(28),
  },
  {
    resource_id: "INC-2026-0129",
    title: "Label OCR fail on outgoing carton",
    owner: "bob",
    description: "Printer ribbon worn; replaced. Monitoring rate.",
    severity: "P4",
    status: "awaiting_review",
    product: "Shipping",
    members: [],
    topics: ["Labelling"],
    attached_agent_config_id: null,
    created_time: nowIso(48 * 60),
    updated_time: nowIso(60),
  },
];

// `owner` is the creator's user id, so the mock mirrors real specstar where
// `created_by` == `owner` at creation time (revision metadata, always present).
const _mockAppItems: AppItem[] = _mockAppItemSeed.map((it) => ({
  ...it,
  created_by: it.owner,
}));

const conversations = new Map<string, Conversation>();

function ensureConversation(id: string): Conversation {
  let conv = conversations.get(id);
  if (!conv) {
    conv = {
      resource_id: `conv-${id}`,
      investigation_id: id,
      messages: [],
    };
    conversations.set(id, conv);
  }
  return conv;
}

// Seed the lead investigation with a few conversation messages so the
// agent panel has content to render.
ensureConversation("INC-2026-0142").messages.push(
  { role: "user", author: "alice", content: "Why did zone-3 drop 8C at 13:30?" },
  {
    role: "assistant",
    author: "RCA Agent",
    content:
      "Set-point trace shows a sudden -8C step at 13:28:42. Compressed-air supply pressure dropped at the same time — likely a stuck cool-air valve.",
    reasoning:
      "Cross-referencing zone-3 set-point with compressed-air pressure trace. The two anomalies coincide within 2 seconds.",
  },
);

const files = new Map<string, Map<string, MockFile>>();
function ensureFiles(id: string): Map<string, MockFile> {
  let m = files.get(id);
  if (!m) {
    m = new Map();
    files.set(id, m);
  }
  return m;
}

// Real directories (incl. empty ones) — mirrors the BE FileStore so the
// file tree behaves identically offline. write() seeds ancestor dirs.
const dirsByWs = new Map<string, Set<string>>();
function ensureDirs(id: string): Set<string> {
  let s = dirsByWs.get(id);
  if (!s) {
    s = new Set();
    dirsByWs.set(id, s);
  }
  return s;
}
function dirAncestors(path: string): string[] {
  const parts = path.replace(/^\/+|\/+$/g, "").split("/");
  return parts.slice(0, -1).map((_, i) => "/" + parts.slice(0, i + 1).join("/"));
}

/** Move/copy a file OR a directory subtree (mirrors the BE _transfer). */
function transferMock(ws: string, from: string, to: string, copy: boolean): void {
  if (to === from || to.startsWith(from + "/")) throw new Error("cannot move into itself");
  const fs = ensureFiles(ws);
  const ds = ensureDirs(ws);
  if (ds.has(from)) {
    if (fs.has(to) || ds.has(to)) throw new Error(`target exists: ${to}`);
    const under = from + "/";
    for (const p of [...fs.keys()]) {
      if (p.startsWith(under)) {
        const dest = to + p.slice(from.length);
        fs.set(dest, { ...fs.get(p)! });
        for (const d of dirAncestors(dest)) ds.add(d);
        if (!copy) fs.delete(p);
      }
    }
    for (const d of [...ds]) {
      if (d === from || d.startsWith(under)) {
        ds.add(to + d.slice(from.length));
        if (!copy) ds.delete(d);
      }
    }
    ds.add(to);
    return;
  }
  const f = fs.get(from);
  if (!f) throw new Error(`not found: ${from}`);
  if (fs.has(to) || ds.has(to)) throw new Error(`target exists: ${to}`);
  fs.set(to, { ...f });
  for (const d of dirAncestors(to)) ds.add(d);
  if (!copy) fs.delete(from);
}

const STARTER_BRIEF = `# Investigation Brief

## Context
Lot 25-W14 saw a step-up in solder bridging on the MX-7 board, first
flagged by AOI at 13:30 on 08-14.

## Hypotheses
- H1: Reflow zone-3 set-point drift
- H2: Stencil aperture wear
- H3: Paste viscosity change with humidity
`;

ensureFiles("INC-2026-0142").set("/brief.md", {
  text: STARTER_BRIEF,
  bytes: STARTER_BRIEF.length,
});

/* ----------------------------- Scripts ----------------------------- */

type ScriptFn = (call: () => string) => AsyncGenerator<AgentEvent>;

async function* happyPath(call: () => string): AsyncGenerator<AgentEvent> {
  yield { type: "agent_metrics", phase: "up", prompt_tokens: 256, completion_tokens: 0, elapsed_ms: 0 };
  await delay(120);
  yield { type: "message_delta", text: "Let me check the latest SPC trace. " };
  yield { type: "agent_metrics", phase: "down", prompt_tokens: 256, completion_tokens: 9, elapsed_ms: 320 };
  await delay(120);
  const id = call();
  yield { type: "tool_start", call_id: id, name: "exec", args: { cmd: ["head", "spc.csv"] } };
  await delay(180);
  yield { type: "tool_end", call_id: id, output: "ts,temp\n13:28:00,237\n13:30:00,229\n" };
  await delay(80);
  yield { type: "message_delta", text: "Yes — a clean step at 13:28:42." };
  yield { type: "agent_metrics", phase: "down", prompt_tokens: 256, completion_tokens: 18, elapsed_ms: 900 };
  await delay(60);
  yield { type: "agent_metrics", phase: "final", prompt_tokens: 251, completion_tokens: 21, elapsed_ms: 1020 };
  yield { type: "done" };
}

async function* cancelScript(): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Starting analysis" };
  await delay(120);
  yield { type: "message_delta", text: "..." };
  await delay(120);
  yield { type: "run_cancelled" };
}

async function* parseErrorScript(call: () => string): AsyncGenerator<AgentEvent> {
  const id = call();
  yield { type: "tool_start", call_id: id, name: "exec", args: { cmd: "<garbled>" } };
  await delay(150);
  yield {
    type: "tool_call_parse_error",
    call_id: id,
    raw: '{"cmd": ["ls"',
    hint: "JSON ended unexpectedly; close the array and object",
  };
  await delay(180);
  const id2 = call();
  yield { type: "tool_start", call_id: id2, name: "exec", args: { cmd: ["ls"] } };
  await delay(200);
  yield { type: "tool_end", call_id: id2, output: "brief.md\nspc.csv\n" };
  yield { type: "done" };
}

async function* maxTurnsScript(): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Still thinking..." };
  await delay(120);
  yield { type: "max_turns_exceeded", turns: 8 };
}

async function* errorScript(): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Trying... " };
  await delay(120);
  yield { type: "error", message: "RuntimeError: simulated failure" };
}

function pickScript(content: string): ScriptFn {
  const c = content.toLowerCase();
  if (c.includes("cancel")) return () => cancelScript();
  if (c.includes("parse")) return (call) => parseErrorScript(call);
  if (c.includes("max") || c.includes("loop")) return () => maxTurnsScript();
  if (c.includes("boom") || c.includes("crash")) return () => errorScript();
  return (call) => happyPath(call);
}

function recordEvent(investigationId: string, ev: AgentEvent): void {
  if (ev.type === "message_delta") {
    const conv = ensureConversation(investigationId);
    const last = conv.messages[conv.messages.length - 1];
    if (last && last.role === "assistant" && !last.tool_call_id) {
      last.content += ev.text;
    } else {
      conv.messages.push({ role: "assistant", author: "RCA Agent", content: ev.text });
    }
    return;
  }
  if (ev.type === "tool_end") {
    const conv = ensureConversation(investigationId);
    conv.messages.push({
      role: "tool",
      content: ev.output,
      tool_call_id: ev.call_id,
    });
  }
}

/* --- #43: per-investigation broadcast pub/sub (mirrors GET .../stream) --- */

const streamSubs = new Map<string, Set<(ev: AgentEvent) => void>>();

function publish(investigationId: string, ev: AgentEvent): void {
  const subs = streamSubs.get(investigationId);
  if (!subs) return;
  for (const fn of [...subs]) fn(ev);
}

let callCounter = 1;

export const mockApi: ApiClient = {
  async getCurrentUser() {
    await delay(10);
    return "default-user";
  },
  async getUsers() {
    await delay(10);
    return [
      { id: "default-user", name: "You", section: "Process Eng", email: "", photo_url: null },
      { id: "alice", name: "Alice Chen", section: "Reflow", email: "", photo_url: null },
      { id: "bob", name: "Bob Liu", section: "SMT", email: "", photo_url: null },
      { id: "carol", name: "Carol Kao", section: "Quality", email: "", photo_url: null },
    ];
  },
  async getNotifications() {
    await delay(10);
    return [];
  },
  async markAllNotificationsRead() {},
  async markNotificationRead() {},
  async addMention(_slug: string, ) {},

  async listApps() {
    await delay(10);
    return [
      {
        slug: "rca",
        title: "Root Cause Analysis",
        description: "Structured failure investigations — SPC, Pareto, reports.",
        icon: "flame",
        color: "#F0502E",
      },
    ];
  },

  async getAppManifest(slug: string): Promise<AppManifest> {
    await delay(10);
    return {
      slug,
      title: "Root Cause Analysis",
      description: "Structured failure investigations — SPC, Pareto, reports.",
      icon: "flame",
      color: "#F0502E",
      function: { workspace: true, sandbox: true, terminal: true },
      agent: {
        picker: [{ preset: "qwen3-local", name: "RCA · Qwen3 (local)" }],
        suggestions: [{ label: "SPC analysis", prompt: "Show the SPC analysis" }],
      },
      item: { noun: "Investigation", noun_plural: "Investigations", create_label: "Start Investigation" },
      layout: {
        breadcrumb: ["severity", "status"],
        statusbar: ["severity", "status", "product"],
        list: ["severity", "status", "product"],
        form: ["severity", "status", "product"],
        default_tabs: ["/SOP.md", "/brief.md"],
        primary_surface: "ide",
        chat_switcher: "auto",
      },
      lifecycle: { status_field: "status", closing_states: ["resolved", "abandoned"] },
      labels: { severity: "Severity", status: "Status", product: "Product" },
      field_styles: {
        severity: { P0: "err", P1: "err", P2: "warn", P3: "ok", P4: "ok" },
        status: { triaging: "warn", awaiting_review: "info", resolved: "ok", abandoned: "muted" },
      },
      fields: [
        { name: "title", label: "Title", kind: "text" },
        { name: "owner", label: "Owner", kind: "text" },
        { name: "description", label: "Description", kind: "text" },
        {
          name: "severity",
          label: "Severity",
          kind: "select",
          options: ["P0", "P1", "P2", "P3", "P4"],
        },
        {
          name: "status",
          label: "Status",
          kind: "select",
          options: ["triaging", "awaiting_review", "resolved", "abandoned"],
        },
        { name: "product", label: "Product", kind: "text" },
      ],
      default_profile: "default",
      profiles: [
        { name: "default", title: "Default", description: "Standard RCA workspace.", upload_dir: "uploads" },
        { name: "tool-demo", title: "Tool demo", description: "Smoke-test the analysis tools.", upload_dir: "uploads" },
        { name: "local-lab", title: "Local lab", description: "RCA SOP sandbox.", upload_dir: "uploads" },
        {
          name: "smt-reflow-example",
          title: "SMT reflow (worked example)",
          description: "A fully-populated worked example.",
          upload_dir: "uploads",
        },
      ],
      resource_route: "/rca-investigation",
    };
  },

  async getToolsCatalog() {
    await delay(10);
    return [
      { name: "exec", label: "Exec", description: "Run a shell command inside the workspace sandbox." },
      {
        name: "ask_knowledge_base",
        label: "Ask Knowledge Base",
        description: "Ask the knowledge-base agent a question about the in-house documents.",
      },
      { name: "read_file", label: "Read File", description: "Read a file from the workspace." },
    ];
  },

  async getItemTools(_slug: string, _itemId: string) {
    await delay(10);
    return [
      {
        key: "exec",
        label: "Exec",
        description: "Run a shell command inside the workspace sandbox.",
        default_on: true,
        pref: "follow" as const,
        effective: true,
      },
      {
        key: "read_file",
        label: "Read File",
        description: "Read a file from the workspace.",
        default_on: true,
        pref: "on" as const,
        effective: true,
      },
      {
        key: "rca-tools",
        label: "Rca Tools",
        description: "Bundled tools: Spc, Pareto.",
        default_on: true,
        pref: "off" as const,
        effective: false,
      },
    ];
  },

  async getItemSkills(_slug: string, _itemId: string) {
    await delay(10);
    return [
      {
        name: "author-skill",
        description: "Co-author a reusable skill with the user.",
        source: "shared",
        default_on: true,
        pref: "follow" as const,
        effective: true,
      },
      {
        name: "report-format",
        description: "How to structure the final report.",
        source: "profile",
        default_on: true,
        pref: "on" as const,
        effective: true,
      },
    ];
  },

  async listAppItems(_resourceRoute: string): Promise<AppItem[]> {
    await delay(10);
    return _mockAppItems;
  },

  async countAppItems(_resourceRoute: string): Promise<number> {
    await delay(10);
    return _mockAppItems.length;
  },

  async getAppItem(_resourceRoute: string, id: string): Promise<AppItem> {
    await delay(10);
    return (
      _mockAppItems.find((it) => it.resource_id === id) ?? {
        resource_id: id,
        title: "Mock item",
        owner: "default-user",
        created_time: nowIso(0),
        created_by: "default-user",
        severity: "P2",
        status: "triaging",
        product: "",
      }
    );
  },

  async createAppItem(_slug: string, body: Record<string, unknown>) {
    await delay(10);
    const resource_id = `rca-investigation/${_mockAppItems.length + 1}`;
    _mockAppItems.push({
      resource_id,
      title: String(body.title ?? ""),
      owner: "default-user",
      created_time: nowIso(0),
      created_by: "default-user",
      severity: body.severity ?? "P2",
      status: "triaging",
      product: body.product ?? "",
    });
    return { resource_id };
  },

  async updateAppItem(_resourceRoute: string, id: string, data: Record<string, unknown>) {
    await delay(10);
    const idx = _mockAppItems.findIndex((it) => it.resource_id === id);
    if (idx >= 0) _mockAppItems[idx] = { ...(data as AppItem), resource_id: id };
    return { resource_id: id };
  },

  async listActivity() {
    await delay(10);
    const now = Date.now();
    return [
      {
        ts: new Date(now - 30 * 60_000).toISOString(),
        kind: "agent_turn_complete",
        text: "Agent finished a turn",
        ref: { investigation_id: "INC-2026-0142" },
      },
      {
        ts: new Date(now - 78 * 60_000).toISOString(),
        kind: "file_written",
        text: "Wrote /report.v1.md",
        ref: { investigation_id: "INC-2026-0142", path: "/report.v1.md" },
      },
    ];
  },

  async getConversation(investigationId) {
    await delay(30);
    const conv = conversations.get(investigationId);
    if (!conv) return null;
    return {
      resource_id: conv.resource_id,
      investigation_id: conv.investigation_id,
      messages: conv.messages.map((m: Message) => ({ ...m })),
    };
  },

  async listFiles(_slug: string, investigationId, prefix) {
    await delay(30);
    const tree = files.get(investigationId);
    if (!tree) return [];
    return Array.from(tree.entries())
      .filter(([path]) => !prefix || path.startsWith(prefix))
      .map(([path, f]) => ({ path, size: f.bytes, read_only: isReadOnlyPath(path) }))
      .sort((a, b) => a.path.localeCompare(b.path));
  },

  async getWorkspaceUsage(_slug: string, investigationId: string) {
    await delay(10);
    const tree = files.get(investigationId);
    const used = tree ? Array.from(tree.values()).reduce((n, f) => n + f.bytes, 0) : 0;
    return { used, quota: 20 * 1024 * 1024 * 1024 }; // 20 GiB, mirrors the default
  },

  async refreshFiles(_slug: string, _investigationId) {
    // Mock has no separate sandbox/snapshot — the in-memory map IS the
    // source of truth. Flushing is a no-op; we just delay a tick.
    await delay(10);
  },

  async readFile(_slug: string, investigationId, path): Promise<FileContent> {
    await delay(30);
    const f = files.get(investigationId)?.get(path);
    if (!f) throw new Error(`file not found: ${path}`);
    if (f.text === null) return { kind: "binary", path, size: f.bytes };
    return { kind: "text", path, size: f.bytes, text: f.text, encoding: "utf-8" };
  },

  fileContentUrl(_slug, investigationId, path) {
    return `/files/${encodeURIComponent(investigationId)}/${path}`;
  },

  async writeFile(_slug: string, investigationId, path, body) {
    await delay(20);
    if (typeof body === "string") {
      ensureFiles(investigationId).set(path, { text: body, bytes: body.length });
    } else if (body instanceof Blob) {
      ensureFiles(investigationId).set(path, { text: null, bytes: body.size });
    } else {
      ensureFiles(investigationId).set(path, { text: null, bytes: body.byteLength });
    }
    for (const d of dirAncestors(path)) ensureDirs(investigationId).add(d);
  },

  async uploadFile(_slug, investigationId, path, body, opts) {
    await delay(20);
    opts?.onProgress?.(body.size, body.size);
    ensureFiles(investigationId).set(path, { text: null, bytes: body.size });
    for (const d of dirAncestors(path)) ensureDirs(investigationId).add(d);
  },

  async *subscribeInvestigation(_slug: string,
    id: string,
    signal?: AbortSignal,
  ): AsyncGenerator<AgentEvent> {
    // #43: a local queue fed by `publish` (and drained here). Mirrors the live
    // long-lived broadcast: every viewer subscribes and sees all turns.
    const queue: AgentEvent[] = [];
    let wake: (() => void) | null = null;
    const push = (ev: AgentEvent) => {
      queue.push(ev);
      wake?.();
    };
    const subs = streamSubs.get(id) ?? new Set();
    subs.add(push);
    streamSubs.set(id, subs);

    const cleanup = () => {
      subs.delete(push);
    };
    try {
      while (!signal?.aborted) {
        if (queue.length === 0) {
          await new Promise<void>((resolve) => {
            wake = resolve;
            if (signal) signal.addEventListener("abort", () => resolve(), { once: true });
          });
          wake = null;
        }
        while (queue.length > 0) {
          if (signal?.aborted) return;
          yield queue.shift()!;
        }
      }
    } finally {
      cleanup();
    }
  },

  async sendMessage(args: SendMessageArgs): Promise<void> {
    // #43: enqueue the turn — record the user message + broadcast it, then run
    // the script publishing each event onto the shared stream (instead of
    // yielding). Cancellation now comes via cancelMessage + DELETE, not a signal.
    ensureConversation(args.investigationId).messages.push({
      role: "user",
      author: "default-user",
      content: args.content,
    });
    publish(args.investigationId, {
      type: "user_message",
      author: "default-user",
      content: args.content,
      created_at: Date.now(),
    });
    const script = pickScript(args.content);
    const call = () => `mock-call-${callCounter++}`;
    try {
      for await (const ev of script(call)) {
        recordEvent(args.investigationId, ev);
        publish(args.investigationId, ev);
      }
    } catch (err) {
      publish(args.investigationId, { type: "error", message: String(err) });
    }
  },

  async *streamCellEvents(args: ExecuteCellArgs): AsyncGenerator<CellEvent> {
    yield { type: "cell_stream", stream: "stdout", text: `# echo: ${args.code.slice(0, 40)}\n` };
    await delay(120);
    yield {
      type: "cell_display_data",
      data: { "text/plain": `mock result for cell ${args.cellIndex}` },
    };
    await delay(80);
    yield { type: "cell_done", execution_count: args.cellIndex + 1 };
  },

  async closeInvestigation(_slug: string, id: string, status: CloseStatus | null) {
    await delay(20);
    const hit = _mockAppItems.find((i) => i.resource_id === id);
    if (!hit) return; // already gone / pure-close on an unseeded item — no-op
    // null = pure close: tear the session down, leave status untouched.
    if (status !== null) hit.status = status;
    hit.updated_time = nowIso();
  },

  async mkdir(_slug: string, investigationId: string, path: string) {
    await delay(10);
    if (ensureFiles(investigationId).has(path)) throw new Error(`file exists at ${path}`);
    const ds = ensureDirs(investigationId);
    ds.add(path);
    for (const d of dirAncestors(path)) ds.add(d);
  },

  async listDirs(_slug: string, investigationId: string) {
    await delay(10);
    return [...ensureDirs(investigationId)].sort();
  },

  async deleteFile(_slug: string, investigationId: string, path: string) {
    await delay(15);
    const ds = ensureDirs(investigationId);
    if (ds.has(path)) {
      // folder delete = remove the subtree (dirs + files under it)
      const under = path + "/";
      for (const d of [...ds]) if (d === path || d.startsWith(under)) ds.delete(d);
      const fs = ensureFiles(investigationId);
      for (const p of [...fs.keys()]) if (p.startsWith(under)) fs.delete(p);
      return;
    }
    files.get(investigationId)?.delete(path); // file delete leaves dirs intact
  },

  async moveFile(_slug: string, investigationId: string, from: string, to: string) {
    await delay(15);
    transferMock(investigationId, from, to, false);
  },

  async copyFile(_slug: string, investigationId: string, from: string, to: string) {
    await delay(15);
    transferMock(investigationId, from, to, true);
  },

  async cancelMessage(_slug: string, _investigationId: string) {
    await delay(10);
  },

  async undoTurns(_slug: string, investigationId: string, turns: number) {
    await delay(10);
    const conv = ensureConversation(investigationId);
    const userIdxs = conv.messages
      .map((m, i) => (m.role === "user" ? i : -1))
      .filter((i) => i >= 0);
    const cut = turns >= userIdxs.length ? 0 : userIdxs[userIdxs.length - turns]!;
    conv.messages = conv.messages.slice(0, cut);
    return { message_count: conv.messages.length };
  },

  async interruptCell(_ref: CellRef) {
    await delay(10);
  },

  async restartKernel(_ref: NotebookRef) {
    await delay(20);
  },

  async searchFiles(_slug: string, investigationId: string, query: string, opts: SearchOptions = {}) {
    await delay(20);
    if (!query) return [];
    const re = compileQuery(query, opts);
    const tree = files.get(investigationId);
    if (!tree) return [];
    const results: SearchResult[] = [];
    for (const [path, f] of [...tree.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      if (f.text === null) continue; // skip binary
      if (!pathSelected(path, opts.include, opts.exclude)) continue;
      const matches = searchLines(f.text, re);
      if (matches.length) results.push({ path, matches });
    }
    return results;
  },

  async replaceInFiles(_slug: string, 
    investigationId: string,
    query: string,
    replacement: string,
    opts: SearchOptions = {},
  ) {
    await delay(20);
    if (!query) return 0;
    const re = compileQuery(query, opts);
    const tree = files.get(investigationId);
    if (!tree) return 0;
    let replaced = 0;
    for (const [path, f] of tree.entries()) {
      if (f.text === null) continue;
      if (!pathSelected(path, opts.include, opts.exclude)) continue;
      let count = 0;
      re.lastIndex = 0;
      const next = f.text.replace(re, () => {
        count += 1;
        return replacement;
      });
      if (count) {
        tree.set(path, { text: next, bytes: next.length });
        replaced += count;
      }
    }
    return replaced;
  },

  async execShell(_slug: string, _investigationId: string, cmd: string[], _signal?: AbortSignal) {
    await delay(40);
    if (cmd.length === 0) {
      return { exit_code: 2, stdout: "", stderr: "empty command\n" };
    }
    const [bin, ...args] = cmd;
    if (bin === "echo") {
      return { exit_code: 0, stdout: `${args.join(" ")}\n`, stderr: "" };
    }
    if (bin === "ls") {
      const inv = _investigationId;
      const paths = [...(files.get(inv)?.keys() ?? [])].sort();
      return { exit_code: 0, stdout: paths.join("\n") + "\n", stderr: "" };
    }
    return {
      exit_code: 127,
      stdout: "",
      stderr: `mock: ${bin}: command not found\n`,
    };
  },
};

/** Internal — exported for tests only. */
export const _mockState = { items: _mockAppItems, conversations, files };
