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
import { parseSseStream } from "./sse";

// specstar auto-CRUD envelopes. Defensive: some endpoints may return a
// bare resource instead of {data: ...}. Plan note in plan-frontend.md
// §F1: verify exact shape against /docs.
type SpecstarResource<T> = { resource_id: string; data: T };
type SpecstarListEnvelope<T> = { data: SpecstarResource<T>[] };

type WorkspaceStruct = {
  name: string;
  description?: string;
  attached_agent_config_id?: string | null;
};

type ConversationStruct = {
  workspace_id: string;
  messages: Message[];
};

function unwrapResource<T>(
  raw: SpecstarResource<T> | { data: SpecstarResource<T> },
): SpecstarResource<T> {
  if ("resource_id" in raw) return raw;
  return raw.data;
}

function toWorkspace(r: SpecstarResource<WorkspaceStruct>): Workspace {
  return {
    resource_id: r.resource_id,
    name: r.data.name,
    description: r.data.description ?? "",
    attached_agent_config_id: r.data.attached_agent_config_id ?? null,
  };
}

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${body.slice(0, 200)}`);
  }
  return resp.json() as Promise<T>;
}

export const realApi: ApiClient = {
  async listWorkspaces(): Promise<Workspace[]> {
    const envelope = await json<SpecstarListEnvelope<WorkspaceStruct>>(
      await fetch("/workspace"),
    );
    return envelope.data.map(toWorkspace);
  },

  async createWorkspace(input: WorkspaceInput): Promise<Workspace> {
    const resp = await fetch("/workspace", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: input.name,
        description: input.description ?? "",
      }),
    });
    const raw = await json<
      | SpecstarResource<WorkspaceStruct>
      | { data: SpecstarResource<WorkspaceStruct> }
    >(resp);
    return toWorkspace(unwrapResource(raw));
  },

  async getConversationByWorkspace(workspaceId: string): Promise<Conversation | null> {
    const envelope = await json<SpecstarListEnvelope<ConversationStruct>>(
      await fetch("/conversation"),
    );
    const hit = envelope.data.find((r) => r.data.workspace_id === workspaceId);
    if (!hit) return null;
    return {
      resource_id: hit.resource_id,
      workspace_id: hit.data.workspace_id,
      messages: hit.data.messages ?? [],
    };
  },

  async listFiles(workspaceId: string): Promise<FileInfo[]> {
    return json<FileInfo[]>(
      await fetch(`/workspaces/${encodeURIComponent(workspaceId)}/files`),
    );
  },

  async readFile(workspaceId: string, path: string): Promise<FileContent> {
    const resp = await fetch(
      `/workspaces/${encodeURIComponent(workspaceId)}/files/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    );
    if (!resp.ok) {
      throw new Error(`read ${path} failed: ${resp.status}`);
    }
    const ctype = resp.headers.get("content-type") ?? "";
    const sizeHeader = resp.headers.get("content-length");
    const size = sizeHeader ? Number.parseInt(sizeHeader, 10) : 0;
    if (ctype.startsWith("text/") || ctype.includes("json") || ctype.includes("xml")) {
      const text = await resp.text();
      return { kind: "text", path, text, size: size || text.length };
    }
    const blob = await resp.blob();
    return { kind: "binary", path, size: blob.size };
  },

  async *streamAgentEvents(args: StreamArgs): AsyncGenerator<AgentEvent> {
    const resp = await fetch(
      `/workspaces/${encodeURIComponent(args.workspaceId)}/messages`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: args.content }),
        signal: args.signal,
      },
    );
    if (!resp.ok || !resp.body) {
      throw new Error(`POST messages failed: ${resp.status}`);
    }
    yield* parseSseStream(resp.body);
  },
};
