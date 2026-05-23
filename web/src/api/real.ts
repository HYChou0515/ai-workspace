/**
 * HTTP client against the live backend. Routes match docs/contract.md §2.
 * Endpoints marked ⏳ in the contract are not yet shipped — when called
 * against the in-progress BE they may 404. Treat list calls as forgiving
 * (return []) so the FE shell is usable while BE renames are landing.
 */

import type { AgentEvent, CellEvent } from "../events";
import { parseSseStream } from "./sse";
import type {
  ApiClient,
  Conversation,
  ExecuteCellArgs,
  FileInfo,
  Investigation,
  InvestigationInput,
  SendMessageArgs,
} from "./types";

// specstar auto-CRUD envelopes.
type SpecstarResource<T> = {
  resource_id: string;
  created_time: string;
  updated_time: string;
  data: T;
};
type SpecstarListEnvelope<T> = { data: SpecstarResource<T>[] };

type InvestigationStruct = {
  title: string;
  owner: string;
  description?: string;
  severity?: Investigation["severity"];
  status?: Investigation["status"];
  product?: string;
  members?: string[];
  topics?: string[];
  attached_agent_config_id?: string | null;
};

type ConversationStruct = {
  investigation_id: string;
  messages: Conversation["messages"];
};

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${body.slice(0, 200)}`);
  }
  return resp.json() as Promise<T>;
}

function unwrap<T>(
  raw: SpecstarResource<T> | { data: SpecstarResource<T> },
): SpecstarResource<T> {
  return "resource_id" in raw ? raw : raw.data;
}

function toInvestigation(r: SpecstarResource<InvestigationStruct>): Investigation {
  return {
    resource_id: r.resource_id,
    created_time: r.created_time,
    updated_time: r.updated_time,
    title: r.data.title,
    owner: r.data.owner,
    description: r.data.description ?? "",
    severity: r.data.severity ?? "P2",
    status: r.data.status ?? "triaging",
    product: r.data.product ?? "",
    members: r.data.members ?? [],
    topics: r.data.topics ?? [],
    attached_agent_config_id: r.data.attached_agent_config_id ?? null,
  };
}

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export const realApi: ApiClient = {
  async listInvestigations() {
    const env = await json<SpecstarListEnvelope<InvestigationStruct>>(
      await fetch("/investigation"),
    );
    return env.data.map(toInvestigation);
  },

  async getInvestigation(id: string) {
    const raw = await json<
      | SpecstarResource<InvestigationStruct>
      | { data: SpecstarResource<InvestigationStruct> }
    >(await fetch(`/investigation/${encodeURIComponent(id)}`));
    return toInvestigation(unwrap(raw));
  },

  async createInvestigation(input: InvestigationInput) {
    const raw = await json<
      | SpecstarResource<InvestigationStruct>
      | { data: SpecstarResource<InvestigationStruct> }
    >(
      await fetch("/investigation", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          title: input.title,
          description: input.description ?? "",
          severity: input.severity ?? "P2",
          product: input.product ?? "",
          topics: input.topics ?? [],
        }),
      }),
    );
    return toInvestigation(unwrap(raw));
  },

  async getConversation(investigationId: string) {
    const env = await json<SpecstarListEnvelope<ConversationStruct>>(
      await fetch("/conversation"),
    );
    const hit = env.data.find((r) => r.data.investigation_id === investigationId);
    if (!hit) return null;
    return {
      resource_id: hit.resource_id,
      investigation_id: hit.data.investigation_id,
      messages: hit.data.messages ?? [],
    };
  },

  async listFiles(investigationId, prefix) {
    const qs = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
    return json<FileInfo[]>(
      await fetch(
        `/investigations/${encodeURIComponent(investigationId)}/files${qs}`,
      ),
    );
  },

  async readFile(investigationId, path) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
    );
    if (!resp.ok) throw new Error(`read ${path} failed: ${resp.status}`);
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

  async writeFile(investigationId, path, body) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
      { method: "PUT", body },
    );
    if (!resp.ok) throw new Error(`write ${path} failed: ${resp.status}`);
  },

  async *streamAgentEvents(args: SendMessageArgs): AsyncGenerator<AgentEvent> {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(args.investigationId)}/messages`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: args.content }),
        signal: args.signal,
      },
    );
    if (!resp.ok || !resp.body) throw new Error(`messages failed: ${resp.status}`);
    yield* parseSseStream(resp.body) as AsyncGenerator<AgentEvent>;
  },

  async *streamCellEvents(args: ExecuteCellArgs): AsyncGenerator<CellEvent> {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(args.investigationId)}/notebooks/${encodePath(args.notebookPath)}/cells/${args.cellIndex}/execute`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code: args.code }),
        signal: args.signal,
      },
    );
    if (!resp.ok || !resp.body) throw new Error(`execute failed: ${resp.status}`);
    // parseSseStream is event-shape-agnostic; cast at the boundary.
    yield* parseSseStream(resp.body) as unknown as AsyncGenerator<CellEvent>;
  },
};
