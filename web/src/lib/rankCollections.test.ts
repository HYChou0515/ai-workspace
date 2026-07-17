import { describe, expect, it } from "vitest";

import type { KbChatSummary, KbCollection } from "../api/kb";
import { rankCollections } from "./rankCollections";

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  is_global: false,
  ...over,
});

const chat = (over: Partial<KbChatSummary>): KbChatSummary => ({
  resource_id: "k1",
  title: "",
  collection_ids: [],
  message_count: 1,
  owner: "me",
  ...over,
});

const ids = (cs: KbCollection[]) => cs.map((c) => c.resource_id);

describe("rankCollections", () => {
  it("ranks the more frequently used collection first", () => {
    const a = coll({ resource_id: "a", name: "A" });
    const b = coll({ resource_id: "b", name: "B" });
    const chats = [
      chat({ collection_ids: ["b"] }),
      chat({ collection_ids: ["b", "a"] }),
      chat({ collection_ids: ["b"] }),
    ];
    // b used 3×, a used 1× → b first.
    expect(ids(rankCollections([a, b], chats, "me"))).toEqual(["b", "a"]);
  });

  it("puts the user's own collections before others when usage ties", () => {
    const mine = coll({ resource_id: "mine", name: "Z-mine", owner: "me" });
    const theirs = coll({ resource_id: "theirs", name: "A-theirs", owner: "someone" });
    // No chats → freq tie at 0; ownership wins over the A→Z name tiebreak.
    expect(ids(rankCollections([theirs, mine], [], "me"))).toEqual(["mine", "theirs"]);
  });

  it("breaks ownership ties by citation count", () => {
    const lo = coll({ resource_id: "lo", name: "A", owner: "me", cited: 2 });
    const hi = coll({ resource_id: "hi", name: "B", owner: "me", cited: 9 });
    expect(ids(rankCollections([lo, hi], [], "me"))).toEqual(["hi", "lo"]);
  });

  it("breaks citation ties by doc_count", () => {
    const small = coll({ resource_id: "s", name: "A", owner: "me", cited: 5, doc_count: 1 });
    const big = coll({ resource_id: "b", name: "B", owner: "me", cited: 5, doc_count: 40 });
    expect(ids(rankCollections([small, big], [], "me"))).toEqual(["b", "s"]);
  });

  it("falls back to an A→Z name tiebreak so the order is fully stable", () => {
    const beta = coll({ resource_id: "x", name: "Beta", owner: "me" });
    const alpha = coll({ resource_id: "y", name: "Alpha", owner: "me" });
    expect(ids(rankCollections([beta, alpha], [], "me"))).toEqual(["y", "x"]);
  });

  it("cold start (no chats) ranks by cited then doc_count", () => {
    const a = coll({ resource_id: "a", name: "A", cited: 1, doc_count: 100 });
    const b = coll({ resource_id: "b", name: "B", cited: 7, doc_count: 1 });
    const c = coll({ resource_id: "c", name: "C", cited: 7, doc_count: 50 });
    // cited: b,c (7) before a (1); within 7, doc_count: c (50) before b (1).
    expect(ids(rankCollections([a, b, c], [], "me"))).toEqual(["c", "b", "a"]);
  });

  it("only counts the user's own chats; a chat owned by someone else is ignored", () => {
    const a = coll({ resource_id: "a", name: "A" });
    const b = coll({ resource_id: "b", name: "B" });
    const chats = [
      chat({ owner: "other", collection_ids: ["a", "a", "a"] as string[] }),
      chat({ owner: "me", collection_ids: ["b"] }),
    ];
    // Only the "me" chat counts → b (1) outranks a (0).
    expect(ids(rankCollections([a, b], chats, "me"))).toEqual(["b", "a"]);
  });

  it("counts a chat with no recorded owner as the user's own", () => {
    const a = coll({ resource_id: "a", name: "A" });
    const b = coll({ resource_id: "b", name: "B" });
    const chats = [chat({ owner: undefined, collection_ids: ["a"] })];
    expect(ids(rankCollections([a, b], chats, "me"))).toEqual(["a", "b"]);
  });
});
