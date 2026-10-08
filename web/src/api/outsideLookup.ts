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

export const outsideLookupApi = {
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
  }): Promise<OutsideAnswerSaved> {
    const form = new FormData();
    form.set("tool_call_id", args.callId);
    form.set("kind", args.answer.kind);
    if (args.answer.kind === "found") {
      form.set("content", args.answer.content);
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

export type OutsideLookupClient = typeof outsideLookupApi;
