/**
 * HTTP client against the live backend. Routes per docs/contract.md §2.
 *
 * Wire format (specstar current):
 *  - `GET /investigation`         → SpecstarEntry<InvestigationStruct>[]
 *  - `GET /investigation/{id}`    → SpecstarEntry<InvestigationStruct>
 *  - `POST /investigation`        → CreateResponse (metadata only — we refetch
 *                                   to obtain the full record).
 *
 * Routes marked ⏳ in contract.md (files, messages, notebooks) are not yet
 * shipped. For list-style endpoints we soften 404 → empty so the FE shell
 * still renders; for streams we surface the error.
 */

import type { AgentEvent, CellEvent } from "../events";
import { parseSseStream } from "./sse";
import type {
  ActivityEntry,
  ApiClient,
  CellRef,
  CloseStatus,
  Conversation,
  ExecResult,
  ExecuteCellArgs,
  FileInfo,
  Investigation,
  InvestigationInput,
  NotebookRef,
  SendMessageArgs,
} from "./types";

type SpecstarRevisionInfo = {
  uid: string;
  resource_id: string;
  revision_id: string;
  created_time: string;
  updated_time: string;
  created_by?: string;
  updated_by?: string;
};

type SpecstarEntry<T> = {
  data: T;
  revision_info: SpecstarRevisionInfo;
  meta?: unknown;
};

type CreateResponse = {
  resource_id: string;
  created_time?: string;
  updated_time?: string;
};

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
    throw new HttpError(resp.status, `${resp.status} ${resp.statusText}: ${body.slice(0, 200)}`);
  }
  return resp.json() as Promise<T>;
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "HttpError";
  }
}

function toInvestigation(e: SpecstarEntry<InvestigationStruct>): Investigation {
  const d = e.data;
  return {
    resource_id: e.revision_info.resource_id,
    created_time: e.revision_info.created_time,
    updated_time: e.revision_info.updated_time,
    title: d.title,
    owner: d.owner,
    description: d.description ?? "",
    severity: d.severity ?? "P2",
    status: d.status ?? "triaging",
    product: d.product ?? "",
    members: d.members ?? [],
    topics: d.topics ?? [],
    attached_agent_config_id: d.attached_agent_config_id ?? null,
  };
}

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export const realApi: ApiClient = {
  async listInvestigations() {
    const arr = await json<SpecstarEntry<InvestigationStruct>[]>(
      await fetch("/investigation"),
    );
    return arr.map(toInvestigation);
  },

  async getInvestigation(id: string) {
    const entry = await json<SpecstarEntry<InvestigationStruct>>(
      await fetch(`/investigation/${encodeURIComponent(id)}`),
    );
    return toInvestigation(entry);
  },

  async createInvestigation(input: InvestigationInput) {
    const resp = await fetch("/investigation", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title: input.title,
        owner: "default-user",
        description: input.description ?? "",
        severity: input.severity ?? "P2",
        status: "triaging",
        product: input.product ?? "",
        members: [],
        topics: input.topics ?? [],
        attached_agent_config_id: null,
        template_profile: input.templateProfile ?? "default",
      }),
    });
    const created = await json<CreateResponse>(resp);
    // Create only returns metadata — refetch to get the full record.
    return this.getInvestigation(created.resource_id);
  },

  async listTemplates() {
    try {
      return await json<string[]>(await fetch("/templates"));
    } catch {
      return ["default"]; // BE older than the templates endpoint
    }
  },

  async listActivity() {
    try {
      return await json<ActivityEntry[]>(await fetch("/activity"));
    } catch {
      return [];
    }
  },

  async closeInvestigation(id: string, status: CloseStatus) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(id)}/close`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status }),
      },
    );
    if (!resp.ok) {
      throw new HttpError(resp.status, `close failed: ${resp.status}`);
    }
  },

  async getConversation(investigationId: string) {
    try {
      const arr = await json<SpecstarEntry<ConversationStruct>[]>(
        await fetch("/conversation"),
      );
      const hit = arr.find((e) => e.data.investigation_id === investigationId);
      if (!hit) return null;
      return {
        resource_id: hit.revision_info.resource_id,
        investigation_id: hit.data.investigation_id,
        messages: hit.data.messages ?? [],
      };
    } catch (err) {
      if (err instanceof HttpError && err.status === 404) return null;
      throw err;
    }
  },

  async listFiles(investigationId, prefix) {
    const qs = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
    try {
      return await json<FileInfo[]>(
        await fetch(
          `/investigations/${encodeURIComponent(investigationId)}/files${qs}`,
        ),
      );
    } catch (err) {
      // The custom files route is not yet shipped (contract.md §2.3 ⏳).
      // Return empty so the workspace shell still renders.
      if (err instanceof HttpError && (err.status === 404 || err.status === 405)) {
        return [];
      }
      throw err;
    }
  },

  async readFile(investigationId, path) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
    );
    if (!resp.ok) throw new HttpError(resp.status, `read ${path} failed: ${resp.status}`);
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
    if (!resp.ok) {
      throw new HttpError(resp.status, `write ${path} failed: ${resp.status}`);
    }
  },

  async deleteFile(investigationId: string, path: string) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
      { method: "DELETE" },
    );
    if (!resp.ok) throw new HttpError(resp.status, `delete ${path} failed: ${resp.status}`);
  },

  async moveFile(investigationId: string, from: string, to: string) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/files/move`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ from, to }),
      },
    );
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new HttpError(resp.status, `move failed: ${resp.status} ${detail.slice(0, 120)}`);
    }
  },

  async copyFile(investigationId: string, from: string, to: string) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/files/copy`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ from, to }),
      },
    );
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new HttpError(resp.status, `copy failed: ${resp.status} ${detail.slice(0, 120)}`);
    }
  },

  async cancelMessage(investigationId: string) {
    // Idempotent on the BE; swallow network/404 noise so a double-click
    // on Stop doesn't surface a scary toast.
    await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/messages/current`,
      { method: "DELETE" },
    ).catch(() => undefined);
  },

  async interruptCell(ref: CellRef) {
    await fetch(
      `/investigations/${encodeURIComponent(ref.investigationId)}/notebooks/${encodePath(ref.notebookPath)}/cells/${ref.cellIndex}/execute`,
      { method: "DELETE" },
    ).catch(() => undefined);
  },

  async restartKernel(ref: NotebookRef) {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(ref.investigationId)}/notebooks/${encodePath(ref.notebookPath)}/kernel/restart`,
      { method: "POST" },
    );
    if (!resp.ok) {
      throw new HttpError(resp.status, `restart failed: ${resp.status}`);
    }
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
    if (!resp.ok || !resp.body) {
      throw new HttpError(resp.status, `messages failed: ${resp.status}`);
    }
    yield* parseSseStream(resp.body) as AsyncGenerator<AgentEvent>;
  },

  async execShell(investigationId: string, cmd: string[]): Promise<ExecResult> {
    const resp = await fetch(
      `/investigations/${encodeURIComponent(investigationId)}/exec`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cmd }),
      },
    );
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new HttpError(resp.status, `exec failed: ${resp.status} ${detail.slice(0, 200)}`);
    }
    return (await resp.json()) as ExecResult;
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
    if (!resp.ok || !resp.body) {
      throw new HttpError(resp.status, `execute failed: ${resp.status}`);
    }
    yield* parseSseStream(resp.body) as unknown as AsyncGenerator<CellEvent>;
  },
};
