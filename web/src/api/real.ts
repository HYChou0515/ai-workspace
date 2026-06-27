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
import { decodeBytes } from "./encoding";
import { API_PREFIX, apiFetch } from "./http";
import { parseSseStream } from "./sse";
import type {
  ActivityEntry,
  AppItem,
  AppManifest,
  AppSummary,
  ApiClient,
  CellRef,
  CloseStatus,
  Conversation,
  ExecResult,
  SearchParams,
  ExecuteCellArgs,
  FileInfo,
  WorkspaceUsage,
  NotebookRef,
  NotificationItem,
  SearchOptions,
  SearchResult,
  SendMessageArgs,
  User,
} from "./types";

type SpecstarRevisionInfo = {
  uid: string;
  resource_id: string;
  revision_id: string;
  created_time: string;
  updated_time: string;
  created_by: string;
  updated_by?: string;
};

type SpecstarEntry<T> = {
  data: T;
  revision_info: SpecstarRevisionInfo;
  meta?: unknown;
};

type ConversationStruct = {
  // #139: the backend `Conversation` struct serializes its owning-item handle
  // as `item_id` (was `investigation_id` pre-#89). Read it under the wire name
  // or `getConversation` matches nothing → the shared chat never hydrates.
  item_id: string;
  messages: Conversation["messages"];
};

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new HttpError(resp.status, `${resp.status} ${resp.statusText}: ${body.slice(0, 200)}`);
  }
  return resp.json() as Promise<T>;
}

/** Serialize SearchParams to a query string (arrays → repeated params, as the
 * specstar list/count endpoints expect). Returns "" when there's nothing. */
function toQuery(params?: SearchParams): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "HttpError";
  }
}

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

/** Map FE SearchOptions → the BE _SearchBody field names. */
function searchBody(opts: SearchOptions): Record<string, unknown> {
  return {
    regex: opts.regex ?? false,
    caseSensitive: opts.caseSensitive ?? false,
    wholeWord: opts.wholeWord ?? false,
    include: opts.include ?? "",
    exclude: opts.exclude ?? "",
  };
}

export const realApi: ApiClient = {
  async getCurrentUser() {
    const me = await json<{ id: string }>(await apiFetch("/me"));
    return me.id;
  },
  async getUsers() {
    return json<User[]>(await apiFetch("/users"));
  },
  async addMention(slug: string, investigationId, userIds, note = "") {
    await apiFetch(`/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/mentions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ user_ids: userIds, note }),
    });
  },
  async getNotifications() {
    return json<NotificationItem[]>(await apiFetch("/notifications"));
  },
  async markAllNotificationsRead() {
    await apiFetch("/notifications/read-all", { method: "POST" });
  },
  async markNotificationRead(id) {
    await apiFetch(`/notifications/${encodeURIComponent(id)}/read`, { method: "POST" });
  },

  async listApps() {
    return json<AppSummary[]>(await apiFetch("/apps"));
  },

  async getAppManifest(slug: string) {
    return json<AppManifest>(await apiFetch(`/apps/${encodeURIComponent(slug)}`));
  },

  async listAppItems(resourceRoute: string, params?: SearchParams) {
    const arr = await json<SpecstarEntry<Record<string, unknown>>[]>(
      await apiFetch(`${resourceRoute}${toQuery(params)}`),
    );
    return arr.map(
      (e): AppItem => ({
        resource_id: e.revision_info.resource_id,
        created_time: e.revision_info.created_time,
        updated_time: e.revision_info.updated_time,
        created_by: e.revision_info.created_by,
        ...(e.data as { title: string; owner: string }),
      }),
    );
  },

  async countAppItems(resourceRoute: string, params?: SearchParams) {
    return json<number>(await apiFetch(`${resourceRoute}/count${toQuery(params)}`));
  },

  async getAppItem(resourceRoute: string, id: string) {
    const e = await json<SpecstarEntry<Record<string, unknown>>>(
      await apiFetch(`${resourceRoute}/${encodeURIComponent(id)}`),
    );
    return {
      resource_id: e.revision_info.resource_id,
      created_time: e.revision_info.created_time,
      updated_time: e.revision_info.updated_time,
      created_by: e.revision_info.created_by,
      ...(e.data as { title: string; owner: string }),
    } satisfies AppItem;
  },

  async createAppItem(slug: string, body: Record<string, unknown>) {
    const resp = await apiFetch(`/a/${encodeURIComponent(slug)}/items`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return json<{ resource_id: string }>(resp);
  },

  async updateAppItem(resourceRoute: string, id: string, data: Record<string, unknown>) {
    // #201: `getAppItem` flattens the server-owned revision metadata onto the
    // item, so a read-modify-write (model picker, inline field edits) holds
    // those keys. specstar rejects `resource_id` in a write body with a 422
    // ("…is immutable"), which would silently drop the whole edit — so PUT only
    // the model struct fields, never the ride-along metadata.
    const body = { ...data };
    for (const k of ["resource_id", "created_time", "updated_time", "created_by"]) delete body[k];
    const resp = await apiFetch(`${resourceRoute}/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return json<{ resource_id: string }>(resp);
  },

  async listActivity() {
    try {
      return await json<ActivityEntry[]>(await apiFetch("/activity"));
    } catch {
      return [];
    }
  },

  async closeInvestigation(slug: string, id: string, status: CloseStatus | null) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(id)}/close`,
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
        await apiFetch("/conversation"),
      );
      const hit = arr.find((e) => e.data.item_id === investigationId);
      if (!hit) return null;
      return {
        resource_id: hit.revision_info.resource_id,
        investigation_id: hit.data.item_id,
        messages: hit.data.messages ?? [],
      };
    } catch (err) {
      if (err instanceof HttpError && err.status === 404) return null;
      throw err;
    }
  },

  async listFiles(slug: string, investigationId, prefix) {
    const qs = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
    try {
      return await json<FileInfo[]>(
        await apiFetch(
          `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files${qs}`,
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

  async getWorkspaceUsage(slug: string, investigationId: string) {
    return json<WorkspaceUsage>(
      await apiFetch(
        `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/usage`,
      ),
    );
  },

  async refreshFiles(slug: string, investigationId) {
    // Server flushes sandbox → snapshot. Swallow 404/405 the same way as
    // listFiles for older backends.
    try {
      await apiFetch(
        `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/refresh`,
        { method: "POST" },
      );
    } catch (err) {
      if (err instanceof HttpError && (err.status === 404 || err.status === 405)) {
        return;
      }
      throw err;
    }
  },

  async readFile(slug: string, investigationId, path) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
    );
    if (!resp.ok) throw new HttpError(resp.status, `read ${path} failed: ${resp.status}`);
    // Read raw bytes and decode losslessly so EVERY file is editable —
    // valid UTF-8 as text, anything else as byte-exact "binary" (latin1).
    const bytes = new Uint8Array(await resp.arrayBuffer());
    const { text, encoding } = decodeBytes(bytes);
    return { kind: "text", path, text, size: bytes.length, encoding };
  },

  fileContentUrl(slug, investigationId, path) {
    // #285: a plain URL the browser fetches directly (image/png content-type),
    // for inline <img>/<a> in the chat — not buffered bytes like readFile.
    return `${API_PREFIX}/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`;
  },

  async writeFile(slug: string, investigationId, path, body) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
      { method: "PUT", body },
    );
    if (!resp.ok) {
      throw new HttpError(resp.status, `write ${path} failed: ${resp.status}`);
    }
  },

  // #198: XHR (not fetch) so the chat attach can report upload progress — fetch has
  // no upload-progress event. Same PUT files endpoint + 413 size-cap semantics.
  uploadFile(slug, investigationId, path, body, opts) {
    return new Promise<void>((resolve, reject) => {
      const url = `${API_PREFIX}/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`;
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      if (opts?.onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) opts.onProgress?.(e.loaded, e.total);
        };
      }
      xhr.onload = () =>
        xhr.status >= 200 && xhr.status < 300
          ? resolve()
          : reject(new HttpError(xhr.status, `write ${path} failed: ${xhr.status}`));
      xhr.onerror = () => reject(new HttpError(0, `write ${path} failed: network error`));
      xhr.send(body);
    });
  },

  async deleteFile(slug: string, investigationId: string, path: string) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/${encodePath(path)}`,
      { method: "DELETE" },
    );
    if (!resp.ok) throw new HttpError(resp.status, `delete ${path} failed: ${resp.status}`);
  },

  async mkdir(slug: string, investigationId: string, path: string) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/mkdir`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path }),
      },
    );
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new HttpError(resp.status, `mkdir failed: ${resp.status} ${detail.slice(0, 120)}`);
    }
  },

  async listDirs(slug: string, investigationId: string) {
    try {
      return await json<string[]>(
        await apiFetch(`/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/dirs`),
      );
    } catch (err) {
      // BE older than the dirs endpoint — degrade to inferred-only dirs.
      if (err instanceof HttpError && (err.status === 404 || err.status === 405)) return [];
      throw err;
    }
  },

  async moveFile(slug: string, investigationId: string, from: string, to: string) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/move`,
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

  async copyFile(slug: string, investigationId: string, from: string, to: string) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/copy`,
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

  async cancelMessage(slug: string, investigationId: string) {
    // Idempotent on the BE; swallow network/404 noise so a double-click
    // on Stop doesn't surface a scary toast.
    await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/messages/current`,
      { method: "DELETE" },
    ).catch(() => undefined);
  },

  async undoTurns(slug: string, investigationId: string, turns: number) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/messages?turns=${turns}`,
      { method: "DELETE" },
    );
    if (!resp.ok) {
      throw new HttpError(resp.status, `undo failed: ${resp.status}`);
    }
    return (await resp.json()) as { message_count: number };
  },

  async interruptCell(ref: CellRef) {
    await apiFetch(
      `/a/${encodeURIComponent(ref.slug)}/items/${encodeURIComponent(ref.investigationId)}/notebooks/${encodePath(ref.notebookPath)}/cells/${ref.cellIndex}/execute`,
      { method: "DELETE" },
    ).catch(() => undefined);
  },

  async restartKernel(ref: NotebookRef) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(ref.slug)}/items/${encodeURIComponent(ref.investigationId)}/notebooks/${encodePath(ref.notebookPath)}/kernel/restart`,
      { method: "POST" },
    );
    if (!resp.ok) {
      throw new HttpError(resp.status, `restart failed: ${resp.status}`);
    }
  },

  async sendMessage(args: SendMessageArgs): Promise<void> {
    // #43: POST no longer streams — it enqueues the turn (202) and returns once
    // accepted. The turn's events arrive on the shared broadcast stream. Don't
    // read the body.
    const resp = await apiFetch(
      `/a/${encodeURIComponent(args.slug)}/items/${encodeURIComponent(args.investigationId)}/messages`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: args.content,
          reasoning_effort: args.reasoningEffort,
          enhancements: args.enhancements,
        }),
        signal: args.signal,
      },
    );
    if (!resp.ok) {
      throw new HttpError(resp.status, `messages failed: ${resp.status}`);
    }
  },

  async *subscribeInvestigation(slug: string, 
    investigationId: string,
    signal?: AbortSignal,
  ): AsyncGenerator<AgentEvent> {
    // #43: the long-lived per-investigation broadcast — every viewer subscribes
    // and sees ALL turns live plus the broadcast-only user_message/file_changed.
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/stream`,
      { signal },
    );
    if (!resp.ok || !resp.body) {
      throw new HttpError(resp.status, `stream failed: ${resp.status}`);
    }
    yield* parseSseStream(resp.body) as AsyncGenerator<AgentEvent>;
  },

  async execShell(slug: string, investigationId: string, cmd: string[], signal?: AbortSignal): Promise<ExecResult> {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/exec`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cmd }),
        signal,
      },
    );
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      throw new HttpError(resp.status, `exec failed: ${resp.status} ${detail.slice(0, 200)}`);
    }
    return (await resp.json()) as ExecResult;
  },

  async searchFiles(slug: string, investigationId: string, query: string, opts: SearchOptions = {}) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/search`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query, ...searchBody(opts) }),
      },
    );
    return json<SearchResult[]>(resp);
  },

  async replaceInFiles(slug: string, 
    investigationId: string,
    query: string,
    replacement: string,
    opts: SearchOptions = {},
  ) {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/replace`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query, replacement, ...searchBody(opts) }),
      },
    );
    const { replaced } = await json<{ replaced: number }>(resp);
    return replaced;
  },

  async *streamCellEvents(args: ExecuteCellArgs): AsyncGenerator<CellEvent> {
    const resp = await apiFetch(
      `/a/${encodeURIComponent(args.slug)}/items/${encodeURIComponent(args.investigationId)}/notebooks/${encodePath(args.notebookPath)}/cells/${args.cellIndex}/execute`,
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
