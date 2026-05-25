import { beforeEach, describe, expect, it } from "vitest";

import { mockKbApi as kb, _resetKbMock } from "./kbMock";

describe("KB api (mock client)", () => {
  beforeEach(() => _resetKbMock());

  it("creates and lists collections", async () => {
    const c = await kb.createCollection("HR policies", "all the rules");
    const listed = await kb.listCollections();
    expect(listed).toContainEqual(c);
    expect(c.name).toBe("HR policies");
  });

  it("uploads a document then lists it, deduping re-uploads", async () => {
    const c = await kb.createCollection("kb");
    const file = new File(["# guide"], "guide.md", { type: "text/markdown" });
    const ids = await kb.uploadDocument(c.resource_id, file);
    expect(ids).toEqual([`${c.resource_id}/me/guide.md`]);

    await kb.uploadDocument(c.resource_id, file); // same name → no duplicate row
    const docs = await kb.listDocuments(c.resource_id);
    expect(docs.map((d) => d.path)).toEqual(["guide.md"]);
  });

  it("preserves a relative path on folder upload", async () => {
    const c = await kb.createCollection("kb");
    const file = new File(["x"], "a.md", { type: "text/markdown" });
    const ids = await kb.uploadDocument(c.resource_id, file, "manuals/reflow/a.md");
    expect(ids).toEqual([`${c.resource_id}/me/manuals/reflow/a.md`]);
    const docs = await kb.listDocuments(c.resource_id);
    expect(docs.map((d) => d.path)).toEqual(["manuals/reflow/a.md"]);
  });

  it("lists a document's chunks with cited counts", async () => {
    const c = await kb.createCollection("kb");
    const file = new File(["# guide\n\nbody"], "guide.md", { type: "text/markdown" });
    const [docId] = await kb.uploadDocument(c.resource_id, file);
    const chunks = await kb.getDocChunks(docId);
    expect(chunks.length).toBeGreaterThan(0);
    expect(chunks[0]).toMatchObject({ seq: 0 });
    expect(typeof chunks[0].cited).toBe("number");
    // sorted by seq
    expect(chunks.map((ch) => ch.seq)).toEqual([...chunks.map((ch) => ch.seq)].sort((a, b) => a - b));
  });

  it("reports indexed chunk count on a listed document", async () => {
    const c = await kb.createCollection("kb");
    const file = new File(["x"], "g.md", { type: "text/markdown" });
    const [docId] = await kb.uploadDocument(c.resource_id, file);
    const docs = await kb.listDocuments(c.resource_id);
    const chunks = await kb.getDocChunks(docId);
    expect(docs[0].chunks).toBe(chunks.length);
  });

  it("creates a chat and lists it with a message count", async () => {
    const c = await kb.createCollection("kb");
    const chat = await kb.createChat("Reflow Q", [c.resource_id]);
    const listed = await kb.listChats();
    expect(listed).toContainEqual({ ...chat, message_count: 0 });
  });

  it("streams a turn, then the refetched chat carries the answer + a citation", async () => {
    const c = await kb.createCollection("kb");
    const chat = await kb.createChat("t", [c.resource_id]);

    const events = [];
    for await (const ev of kb.streamMessage({ chatId: chat.resource_id, content: "why voids?" })) {
      events.push(ev);
    }
    expect(events.at(-1)).toEqual({ type: "done" });
    expect(events.some((e) => e.type === "message_delta")).toBe(true);

    const detail = await kb.getChat(chat.resource_id);
    expect(detail.messages.map((m) => m.role)).toEqual(["user", "tool", "assistant"]);
    const answer = detail.messages.find((m) => m.role === "assistant")!;
    expect(answer.content).toContain("[1]");
    expect(answer.citations[0].filename).toBe("reflow.md");
  });

  it("deletes a chat", async () => {
    const chat = await kb.createChat("t", []);
    await kb.deleteChat(chat.resource_id);
    expect(await kb.listChats()).toEqual([]);
  });
});
