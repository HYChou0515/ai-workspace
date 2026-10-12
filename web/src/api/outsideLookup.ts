/**
 * The "請幫我查" card's two calls (docs/plan-outside-lookup.md): the deploy's
 * search buttons, and the answer — saved under `lookups/` and sent as the
 * message that answers the card, in one request.
 */
import { apiFetch, detailSentence, httpErrorFrom } from "./http";

/** A search button: `url` holds `{q}`, where the query goes. */
export type LookupTarget = { name: string; url: string };

export type OutsideAnswer =
  | {
      kind: "found";
      content: string;
      sourceUrl: string;
      /** The search button last pressed, if any — recorded in the file. */
      target: string;
      attachments: File[];
    }
  | { kind: "not_found"; reason: string };

export type OutsideAnswerSaved = { path: string | null; attachments: string[] };

const enc = encodeURIComponent;

/** The search address for `query` — every `{q}`, encoded. */
export function searchUrl(target: LookupTarget, query: string): string {
  return target.url.split("{q}").join(enc(query));
}

/** A lookup the card handed to the person's outside helper
 * (docs/plan-outside-helper.md §3.2). */
export type HelperJob = {
  id: string;
  kind: "search" | "open_url";
  args: string;
  state: "queued" | "claimed" | "done" | "failed" | "cancelled";
  error: string;
  /** What the helper waits on while it holds the job: `captcha`, or "". */
  note: string;
};

/** A packed helper the operator published (docs/plan-outside-helper.md §3.7.2). */
export type HelperRelease = {
  version: string;
  min_version: string;
  /** The zip's file name; downloaded from `/outside-helper/download/<file>`. */
  file: string;
  sha256: string;
  /** Bytes. */
  size: number;
};

type CardArgs = { slug: string; itemId: string; chatId?: string; callId: string };

function cardJobs(args: CardArgs): string {
  const item = `/a/${enc(args.slug)}/items/${enc(args.itemId)}`;
  const chat = args.chatId ? `/chats/${enc(args.chatId)}` : "";
  return `${item}${chat}/outside-jobs`;
}

async function refused(r: Response, fallback: string): Promise<never> {
  const why = await detailSentence(r.clone());
  throw await httpErrorFrom(r, why ?? fallback);
}

export const outsideLookupApi = {
  /** Whether the person's own helper checked in just now (D7). */
  async helperStatus(): Promise<{ online: boolean }> {
    const r = await apiFetch("/outside-helper/status");
    if (!r.ok) return refused(r, `helper status failed: ${r.status}`);
    return (await r.json()) as { online: boolean };
  },

  /** Hand the card to the helper. `auto` is the card asking on its own, with
   * no 「查」 — the server allows it only for a URL an earlier lookup in this
   * chat brought back (D12) and answers 409 otherwise. */
  async handOver(args: CardArgs & { query?: string; auto: boolean }): Promise<HelperJob> {
    const r = await apiFetch(cardJobs(args), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tool_call_id: args.callId, query: args.query ?? "", auto: args.auto }),
    });
    if (!r.ok) return refused(r, `hand over failed: ${r.status}`);
    return (await r.json()) as HelperJob;
  },

  async job(args: CardArgs): Promise<HelperJob> {
    const r = await apiFetch(`${cardJobs(args)}/${enc(args.callId)}`);
    if (!r.ok) return refused(r, `helper job failed: ${r.status}`);
    return (await r.json()) as HelperJob;
  },

  async cancel(args: CardArgs): Promise<void> {
    const r = await apiFetch(`${cardJobs(args)}/${enc(args.callId)}`, { method: "DELETE" });
    if (!r.ok && r.status !== 404) return refused(r, `cancel failed: ${r.status}`);
  },

  /** The helper the operator published (§3.7.2), or null when there is none. */
  async release(): Promise<HelperRelease | null> {
    const r = await apiFetch("/outside-helper/manifest");
    if (r.status === 404) return null;
    if (!r.ok) return refused(r, `helper release failed: ${r.status}`);
    return (await r.json()) as HelperRelease;
  },

  async targets(): Promise<LookupTarget[]> {
    const r = await apiFetch("/lookup-targets");
    if (!r.ok) throw await httpErrorFrom(r, `search buttons failed: ${r.status}`);
    return (await r.json()) as LookupTarget[];
  },

  async answer(args: {
    slug: string;
    itemId: string;
    /** The chat the card is in; absent ⇒ the item's default chat. */
    chatId?: string;
    callId: string;
    answer: OutsideAnswer;
    /** What was searched — the query as the person edited it (D8). */
    query?: string;
    /** The person's own date, `YYYY-MM-DD`, for the saved file's name. */
    date?: string;
  }): Promise<OutsideAnswerSaved> {
    const form = new FormData();
    form.set("tool_call_id", args.callId);
    form.set("kind", args.answer.kind);
    if (args.query) form.set("query", args.query);
    if (args.date) form.set("date", args.date);
    if (args.answer.kind === "found") {
      // A FILE part, not a field: the server's form parser caps a plain field
      // at 1 MiB, and one pasted page can pass that.
      form.set(
        "content",
        new File([args.answer.content], "content.md", { type: "text/markdown;charset=utf-8" }),
      );
      form.set("source_url", args.answer.sourceUrl);
      form.set("target", args.answer.target);
      for (const f of args.answer.attachments) form.append("attachments", f, f.name);
    } else {
      form.set("reason", args.answer.reason);
    }
    const item = `/a/${enc(args.slug)}/items/${enc(args.itemId)}`;
    const chat = args.chatId ? `/chats/${enc(args.chatId)}` : "";
    const r = await apiFetch(`${item}${chat}/outside-answers`, { method: "POST", body: form });
    if (!r.ok) {
      // The server says WHY (already answered, may not add files, nothing to
      // send); the card shows that sentence rather than a status number.
      const why = await detailSentence(r.clone());
      throw await httpErrorFrom(r, why ?? `answer failed: ${r.status}`);
    }
    return (await r.json()) as OutsideAnswerSaved;
  },
};

/** What the card needs. The helper's calls are optional: a client without
 * them (a test, a surface with no helper) reads as "not connected" (D7). */
export type OutsideLookupClient = Pick<typeof outsideLookupApi, "targets" | "answer"> &
  Partial<Pick<typeof outsideLookupApi, "helperStatus" | "handOver" | "job" | "cancel">>;
