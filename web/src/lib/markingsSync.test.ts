/**
 * #847 P7 review round 1: chat mode opens views on the editor-area page, in a
 * NEW TAB, while the composer lives in the chat tab. Each tab has its own
 * store, so without this a marking made on that page could never be sent.
 * The stores of one item sync across tabs; another item's never do.
 */
import { afterEach, describe, expect, it } from "vitest";

import { MarkingStore } from "./markings";
import { syncMarkingsAcrossTabs } from "./markingsSync";

const tick = () => new Promise((r) => setTimeout(r, 20));
const offs: (() => void)[] = [];
afterEach(() => {
  for (const off of offs.splice(0)) off();
});

function tab(item: string) {
  const store = new MarkingStore();
  offs.push(syncMarkingsAcrossTabs(store, item));
  return store;
}

describe("syncMarkingsAcrossTabs", () => {
  it("a marking made in one tab reaches the other tab of the same item", async () => {
    const page = tab("pm/1");
    const chat = tab("pm/1");
    page.set("fail", { lot: new Set(["L1", "L2"]) }, "/v/grid.ai.yaml");
    await tick();
    expect([...chat.get("fail")!.marking.lot!].sort()).toEqual(["L1", "L2"]);
    expect(chat.get("fail")!.source).toBe("/v/grid.ai.yaml");
  });

  it("clearing travels too", async () => {
    const page = tab("pm/1");
    const chat = tab("pm/1");
    page.set("fail", { lot: new Set(["L1"]) }, null);
    await tick();
    page.set("fail", null, null);
    await tick();
    expect(chat.get("fail")).toBeUndefined();
  });

  it("a tab that opens later gets what the others already hold", async () => {
    const chat = tab("pm/1");
    chat.set("fail", { lot: new Set(["L1"]) }, null);
    const page = tab("pm/1");
    await tick();
    expect(page.names()).toEqual(["fail"]);
  });

  it("another item's tabs never see it", async () => {
    const a = tab("pm/1");
    const b = tab("pm/2");
    a.set("fail", { lot: new Set(["L1"]) }, null);
    await tick();
    expect(b.names()).toEqual([]);
  });

  it("a write received from a peer is not sent back (no echo storm)", async () => {
    const page = tab("pm/1");
    const chat = tab("pm/1");
    let heard = 0;
    chat.subscribe("fail", () => heard++);
    page.set("fail", { lot: new Set(["L1"]) }, null);
    await tick();
    await tick();
    expect(heard).toBe(1);
  });

  it("ignores a message that is not the shape it sends, rather than throwing", async () => {
    const chat = tab("pm/1");
    const stray = new BroadcastChannel("aiws-markings:pm/1");
    for (const junk of [null, "x", { kind: "set" }, { kind: "set", name: "a", columns: 3 }]) {
      stray.postMessage(junk);
    }
    stray.postMessage({ kind: "set", name: "ok", columns: { lot: ["L1"] }, source: null });
    await tick();
    stray.close();
    expect(chat.names()).toEqual(["ok"]);
  });

  it("is a no-op where BroadcastChannel does not exist", () => {
    const saved = globalThis.BroadcastChannel;
    // @ts-expect-error — simulating an environment without it
    delete globalThis.BroadcastChannel;
    try {
      const off = syncMarkingsAcrossTabs(new MarkingStore(), "pm/1");
      off();
    } finally {
      globalThis.BroadcastChannel = saved;
    }
  });
});
