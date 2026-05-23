/**
 * In-memory mock backend. Selected via `VITE_USE_MOCK=1`. Lifetime =
 * page session — refresh wipes state. Wire shapes follow contract.md.
 *
 * Seed data is realistic enough to populate the Home table and exercise
 * filter / count logic; the streaming scripts mirror the variants in
 * AgentEvent so the agent panel can be developed offline.
 */

import type { AgentEvent, CellEvent } from "../events";
import type {
  AgentConfigInfo,
  ApiClient,
  CellRef,
  CloseStatus,
  Conversation,
  ExecuteCellArgs,
  FileContent,
  Investigation,
  InvestigationInput,
  Message,
  NotebookRef,
  SendMessageArgs,
} from "./types";

type MockFile = { text: string | null; bytes: number };

function nowIso(offsetMin = 0): string {
  return new Date(Date.now() - offsetMin * 60_000).toISOString();
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

const investigations: Investigation[] = [
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

const agentConfigs: AgentConfigInfo[] = [
  { resource_id: "agent-config:qwen-local", name: "RCA · Qwen3 (local)", model: "ollama_chat/qwen3:14b" },
  { resource_id: "agent-config:claude-opus", name: "RCA · Claude Opus", model: "claude-opus-4-7" },
];

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
  yield { type: "message_delta", text: "Let me check the latest SPC trace. " };
  await delay(120);
  const id = call();
  yield { type: "tool_start", call_id: id, name: "exec", args: { cmd: ["head", "spc.csv"] } };
  await delay(180);
  yield { type: "tool_end", call_id: id, output: "ts,temp\n13:28:00,237\n13:30:00,229\n" };
  await delay(80);
  yield { type: "message_delta", text: "Yes — a clean step at 13:28:42." };
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

let callCounter = 1;
let createCounter = 200;

export const mockApi: ApiClient = {
  async listInvestigations() {
    await delay(40);
    return investigations.map((i) => ({ ...i }));
  },

  async getInvestigation(id) {
    await delay(30);
    const hit = investigations.find((i) => i.resource_id === id);
    if (!hit) throw new Error(`not found: ${id}`);
    return { ...hit };
  },

  async createInvestigation(input: InvestigationInput) {
    await delay(60);
    const id = `INC-2026-${String(createCounter++).padStart(4, "0")}`;
    const inv: Investigation = {
      resource_id: id,
      created_time: nowIso(),
      updated_time: nowIso(),
      title: input.title,
      owner: "default-user",
      description: input.description ?? "",
      severity: input.severity ?? "P2",
      status: "triaging",
      product: input.product ?? "",
      members: [],
      topics: input.topics ?? [],
      attached_agent_config_id: null,
    };
    investigations.unshift(inv);
    return { ...inv };
  },

  async listAgentConfigs() {
    await delay(10);
    return agentConfigs.map((c) => ({ ...c }));
  },

  async attachAgentConfig(investigationId: string, configId: string | null) {
    await delay(10);
    const hit = investigations.find((i) => i.resource_id === investigationId);
    if (!hit) throw new Error(`not found: ${investigationId}`);
    hit.attached_agent_config_id = configId;
    hit.updated_time = nowIso();
  },

  async listTemplates() {
    await delay(10);
    return ["default", "smt-reflow-example"];
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

  async listFiles(investigationId, prefix) {
    await delay(30);
    const tree = files.get(investigationId);
    if (!tree) return [];
    return Array.from(tree.entries())
      .filter(([path]) => !prefix || path.startsWith(prefix))
      .map(([path, f]) => ({ path, size: f.bytes }))
      .sort((a, b) => a.path.localeCompare(b.path));
  },

  async readFile(investigationId, path): Promise<FileContent> {
    await delay(30);
    const f = files.get(investigationId)?.get(path);
    if (!f) throw new Error(`file not found: ${path}`);
    if (f.text === null) return { kind: "binary", path, size: f.bytes };
    return { kind: "text", path, size: f.bytes, text: f.text };
  },

  async writeFile(investigationId, path, body) {
    await delay(20);
    if (typeof body === "string") {
      ensureFiles(investigationId).set(path, { text: body, bytes: body.length });
    } else if (body instanceof Blob) {
      ensureFiles(investigationId).set(path, { text: null, bytes: body.size });
    } else {
      ensureFiles(investigationId).set(path, { text: null, bytes: body.byteLength });
    }
  },

  async *streamAgentEvents(args: SendMessageArgs) {
    ensureConversation(args.investigationId).messages.push({
      role: "user",
      author: "default-user",
      content: args.content,
    });
    const script = pickScript(args.content);
    const call = () => `mock-call-${callCounter++}`;
    try {
      for await (const ev of script(call)) {
        if (args.signal?.aborted) {
          yield { type: "run_cancelled" } as AgentEvent;
          return;
        }
        recordEvent(args.investigationId, ev);
        yield ev;
      }
    } catch (err) {
      yield { type: "error", message: String(err) };
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

  async closeInvestigation(id: string, status: CloseStatus | null) {
    await delay(20);
    const hit = investigations.find((i) => i.resource_id === id);
    if (!hit) throw new Error(`not found: ${id}`);
    // null = pure close: tear the session down, leave status untouched.
    if (status !== null) hit.status = status;
    hit.updated_time = nowIso();
  },

  async deleteFile(investigationId: string, path: string) {
    await delay(15);
    files.get(investigationId)?.delete(path);
  },

  async moveFile(investigationId: string, from: string, to: string) {
    await delay(15);
    const fs = ensureFiles(investigationId);
    const f = fs.get(from);
    if (!f) throw new Error(`not found: ${from}`);
    if (fs.has(to)) throw new Error(`target exists: ${to}`);
    fs.set(to, f);
    fs.delete(from);
  },

  async copyFile(investigationId: string, from: string, to: string) {
    await delay(15);
    const fs = ensureFiles(investigationId);
    const f = fs.get(from);
    if (!f) throw new Error(`not found: ${from}`);
    if (fs.has(to)) throw new Error(`target exists: ${to}`);
    fs.set(to, { ...f });
  },

  async cancelMessage(_investigationId: string) {
    await delay(10);
  },

  async interruptCell(_ref: CellRef) {
    await delay(10);
  },

  async restartKernel(_ref: NotebookRef) {
    await delay(20);
  },

  async execShell(_investigationId: string, cmd: string[]) {
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
export const _mockState = { investigations, conversations, files };
