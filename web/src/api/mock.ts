import type { AgentEvent } from "../events";
import type {
  ApiClient,
  Conversation,
  FileContent,
  FileInfo,
  Message,
  StreamArgs,
  Workspace,
  WorkspaceInput,
} from "./types";

// In-memory mock state. Lifetime = page session; refresh wipes it.
// Wire shapes match what plan-backend.md §4 promises real BE will return.

type MockFile = { text: string | null; bytes: number };

type MockWorkspace = Workspace & { conversation_id: string };

const STARTER_README = `# Mock workspace

This page is talking to a mock backend (\`VITE_USE_MOCK=1\`).

Try a few prompts to see different SSE paths:
- "hello" — happy path
- "write something" — write_file tool, file list refreshes
- "exec ls" — exec tool round-trip
- "cancel this" — user interrupt -> run_cancelled
- "go idle" — sandbox_killed_idle banner mid-stream
- "parse error" — tool_call_parse_error, then retry
- "loop forever" — max_turns_exceeded
- "boom" — generic error
`;

const STARTER_MAIN_PY = `def main() -> None:
    print("hello from the mock workspace")


if __name__ == "__main__":
    main()
`;

const workspaces: MockWorkspace[] = [
  {
    resource_id: "ws-readme",
    name: "Read the README",
    description: "Starter workspace with seeded files + conversation.",
    attached_agent_config_id: null,
    conversation_id: "conv-readme",
  },
  {
    resource_id: "ws-empty",
    name: "Empty playground",
    description: "Nothing here yet — write your first message.",
    attached_agent_config_id: null,
    conversation_id: "conv-empty",
  },
];

const conversations = new Map<string, Conversation>([
  [
    "ws-readme",
    {
      resource_id: "conv-readme",
      workspace_id: "ws-readme",
      messages: [
        { role: "user", content: "show me what's in the workspace" },
        {
          role: "assistant",
          content: "There are two files: README.md and src/main.py.",
        },
        {
          role: "tool",
          content: "README.md\nsrc/main.py\ndata/large.bin",
          tool_name: "ls",
          tool_call_id: "seed-call-1",
        },
      ],
    },
  ],
]);

const files = new Map<string, Map<string, MockFile>>([
  [
    "ws-readme",
    new Map<string, MockFile>([
      ["README.md", { text: STARTER_README, bytes: STARTER_README.length }],
      ["src/main.py", { text: STARTER_MAIN_PY, bytes: STARTER_MAIN_PY.length }],
      ["data/large.bin", { text: null, bytes: 1_048_576 }],
    ]),
  ],
  ["ws-empty", new Map<string, MockFile>()],
]);

let workspaceCounter = 1;
let callCounter = 1;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function ensureConversation(workspaceId: string): Conversation {
  let conv = conversations.get(workspaceId);
  if (!conv) {
    conv = {
      resource_id: `conv-${workspaceId}`,
      workspace_id: workspaceId,
      messages: [],
    };
    conversations.set(workspaceId, conv);
  }
  return conv;
}

function appendMessage(workspaceId: string, msg: Message): void {
  ensureConversation(workspaceId).messages.push(msg);
}

function toPublicWorkspace(ws: MockWorkspace): Workspace {
  return {
    resource_id: ws.resource_id,
    name: ws.name,
    description: ws.description,
    attached_agent_config_id: ws.attached_agent_config_id,
  };
}

/** Script chosen by keyword in the user content. */
type Script = (call: () => string) => AsyncGenerator<AgentEvent>;

async function* happyPath(call: () => string): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Sure, " };
  await delay(120);
  yield { type: "message_delta", text: "let me think about that. " };
  await delay(120);
  const id = call();
  yield { type: "tool_start", call_id: id, name: "echo", args: { text: "hi" } };
  await delay(180);
  yield { type: "tool_end", call_id: id, output: "hi" };
  await delay(80);
  yield { type: "message_delta", text: "Done." };
  yield { type: "done" };
}

async function* writeScript(
  workspaceId: string,
  call: () => string,
): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "I'll write a note for you. " };
  await delay(150);
  const id = call();
  const path = `notes/note-${Date.now()}.md`;
  const content = `# Note\n\nwritten at ${new Date().toISOString()}\n`;
  yield {
    type: "tool_start",
    call_id: id,
    name: "write_file",
    args: { path, content },
  };
  await delay(180);
  // Persist into mock file map so F3 refresh sees it.
  files.get(workspaceId)?.set(path, { text: content, bytes: content.length });
  yield { type: "tool_end", call_id: id, output: `wrote ${content.length} bytes to ${path}` };
  await delay(80);
  yield { type: "message_delta", text: `Wrote ${path}.` };
  yield { type: "done" };
}

async function* execScript(call: () => string): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Running it. " };
  await delay(120);
  const id = call();
  yield {
    type: "tool_start",
    call_id: id,
    name: "exec",
    args: { cmd: ["ls", "-1"] },
  };
  await delay(220);
  yield { type: "tool_end", call_id: id, output: "README.md\nsrc\ndata\n" };
  await delay(80);
  yield { type: "message_delta", text: "That's the listing." };
  yield { type: "done" };
}

async function* cancelScript(): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Starting a long task" };
  await delay(120);
  yield { type: "message_delta", text: "..." };
  await delay(120);
  yield { type: "run_cancelled" };
}

async function* idleScript(call: () => string): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Hmm, the sandbox dozed off. " };
  await delay(120);
  yield { type: "sandbox_killed_idle" };
  await delay(120);
  const id = call();
  yield {
    type: "tool_start",
    call_id: id,
    name: "exec",
    args: { cmd: ["echo", "cold start"] },
  };
  await delay(220);
  yield { type: "tool_end", call_id: id, output: "cold start" };
  yield { type: "message_delta", text: " Back in business." };
  yield { type: "done" };
}

async function* parseErrorScript(call: () => string): AsyncGenerator<AgentEvent> {
  const id = call();
  yield {
    type: "tool_start",
    call_id: id,
    name: "exec",
    args: { cmd: "<garbled>" },
  };
  await delay(150);
  yield {
    type: "tool_call_parse_error",
    call_id: id,
    raw: '{"cmd": ["ls"',
    hint: "JSON ended unexpectedly; close the array and object",
  };
  await delay(180);
  const id2 = call();
  yield {
    type: "tool_start",
    call_id: id2,
    name: "exec",
    args: { cmd: ["ls"] },
  };
  await delay(200);
  yield { type: "tool_end", call_id: id2, output: "fixed-and-ran" };
  yield { type: "message_delta", text: "Recovered from a parse error." };
  yield { type: "done" };
}

async function* maxTurnsScript(): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Thinking..." };
  await delay(120);
  yield { type: "message_delta", text: " still thinking..." };
  await delay(120);
  yield { type: "max_turns_exceeded", turns: 8 };
}

async function* errorScript(): AsyncGenerator<AgentEvent> {
  yield { type: "message_delta", text: "Trying... " };
  await delay(120);
  yield { type: "error", message: "RuntimeError: simulated failure" };
}

function pickScript(content: string, workspaceId: string): Script {
  const c = content.toLowerCase();
  if (c.includes("cancel")) return () => cancelScript();
  if (c.includes("idle")) return (call) => idleScript(call);
  if (c.includes("parse")) return (call) => parseErrorScript(call);
  if (c.includes("max") || c.includes("loop")) return () => maxTurnsScript();
  if (c.includes("boom") || c.includes("crash")) return () => errorScript();
  if (c.includes("write")) return (call) => writeScript(workspaceId, call);
  if (c.includes("exec") || c.includes("run") || c.includes("ls"))
    return (call) => execScript(call);
  return (call) => happyPath(call);
}

/**
 * Accumulate the events of a single turn into the conversation history,
 * mirroring what backend would persist (see plan-backend.md /workspaces/...
 * /messages handler).
 */
function recordEvent(workspaceId: string, ev: AgentEvent): void {
  if (ev.type === "message_delta") {
    const conv = ensureConversation(workspaceId);
    const last = conv.messages[conv.messages.length - 1];
    if (last && last.role === "assistant" && !last.tool_call_id) {
      last.content += ev.text;
    } else {
      conv.messages.push({ role: "assistant", content: ev.text });
    }
    return;
  }
  if (ev.type === "tool_end") {
    appendMessage(workspaceId, {
      role: "tool",
      content: ev.output,
      tool_call_id: ev.call_id,
    });
  }
}

export const mockApi: ApiClient = {
  async listWorkspaces(): Promise<Workspace[]> {
    await delay(40);
    return workspaces.map(toPublicWorkspace);
  },

  async createWorkspace(input: WorkspaceInput): Promise<Workspace> {
    await delay(60);
    const id = `ws-mock-${workspaceCounter++}`;
    const ws: MockWorkspace = {
      resource_id: id,
      name: input.name,
      description: input.description ?? "",
      attached_agent_config_id: null,
      conversation_id: `conv-${id}`,
    };
    workspaces.unshift(ws);
    files.set(id, new Map());
    return toPublicWorkspace(ws);
  },

  async getConversationByWorkspace(workspaceId: string): Promise<Conversation | null> {
    await delay(30);
    const conv = conversations.get(workspaceId);
    if (!conv) return null;
    return {
      resource_id: conv.resource_id,
      workspace_id: conv.workspace_id,
      messages: conv.messages.map((m) => ({ ...m })),
    };
  },

  async listFiles(workspaceId: string): Promise<FileInfo[]> {
    await delay(30);
    const tree = files.get(workspaceId);
    if (!tree) return [];
    return Array.from(tree.entries())
      .map(([path, f]) => ({ path, size: f.bytes }))
      .sort((a, b) => a.path.localeCompare(b.path));
  },

  async readFile(workspaceId: string, path: string): Promise<FileContent> {
    await delay(40);
    const f = files.get(workspaceId)?.get(path);
    if (!f) throw new Error(`file not found: ${path}`);
    if (f.text === null) return { kind: "binary", path, size: f.bytes };
    return { kind: "text", path, size: f.bytes, text: f.text };
  },

  async *streamAgentEvents(args: StreamArgs): AsyncGenerator<AgentEvent> {
    appendMessage(args.workspaceId, { role: "user", content: args.content });
    const script = pickScript(args.content, args.workspaceId);
    const call = () => `mock-call-${callCounter++}`;
    try {
      for await (const ev of script(call)) {
        if (args.signal?.aborted) {
          yield { type: "run_cancelled" };
          return;
        }
        recordEvent(args.workspaceId, ev);
        yield ev;
      }
    } catch (err) {
      yield { type: "error", message: String(err) };
    }
  },
};
