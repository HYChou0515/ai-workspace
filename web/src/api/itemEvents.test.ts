/**
 * #856 P9 (live check): every open `.ai.yaml` view subscribed to the item's
 * `/stream` on its own connection. A layout of four charts plus the agent's and
 * the presence stream reached the browser's six-per-host limit, and the next
 * request — sending a message — queued forever. One connection per item per
 * page, shared by every subscriber.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./index";
import { subscribeItemEvents } from "./itemEvents";
import type { AgentEvent } from "../events";

type Ctl = { push: (ev: AgentEvent) => void; signal: AbortSignal };

function fakeStreams() {
  const opened: Ctl[] = [];
  const spy = vi.spyOn(api, "subscribeInvestigation").mockImplementation(
    (_slug: string, _id: string, signal?: AbortSignal) => {
      const queue: AgentEvent[] = [];
      let wake: (() => void) | null = null;
      opened.push({
        push: (ev) => {
          queue.push(ev);
          wake?.();
        },
        signal: signal!,
      });
      return (async function* () {
        while (!signal?.aborted) {
          if (queue.length) {
            yield queue.shift()!;
            continue;
          }
          await new Promise<void>((r) => {
            wake = r;
            signal?.addEventListener("abort", () => r(), { once: true });
          });
        }
        throw Object.assign(new Error("aborted"), { name: "AbortError" });
      })();
    },
  );
  return { opened, spy };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const changed: AgentEvent = { type: "file_changed", path: "/a", by: "u", kind: "written" };

afterEach(() => vi.restoreAllMocks());

describe("subscribeItemEvents", () => {
  it("shares one connection among every subscriber of an item", async () => {
    const { opened, spy } = fakeStreams();
    const got: string[] = [];
    const offs = [1, 2, 3, 4].map((n) => subscribeItemEvents("rca", "I1", () => got.push(`v${n}`)));
    await tick();
    expect(spy).toHaveBeenCalledTimes(1);
    opened[0]!.push(changed);
    await tick();
    expect(got.sort()).toEqual(["v1", "v2", "v3", "v4"]);
    offs.forEach((off) => off());
  });

  it("closes the connection when the last subscriber leaves, not before", async () => {
    const { opened } = fakeStreams();
    const a = subscribeItemEvents("rca", "I1", () => {});
    const b = subscribeItemEvents("rca", "I1", () => {});
    await tick();
    a();
    expect(opened[0]!.signal.aborted).toBe(false);
    b();
    expect(opened[0]!.signal.aborted).toBe(true);
  });

  it("another item gets its own connection", async () => {
    const { spy } = fakeStreams();
    const a = subscribeItemEvents("rca", "I1", () => {});
    const b = subscribeItemEvents("rca", "I2", () => {});
    await tick();
    expect(spy).toHaveBeenCalledTimes(2);
    a();
    b();
  });

  it("a subscriber that leaves hears nothing more; the others still do", async () => {
    const { opened } = fakeStreams();
    const got: string[] = [];
    const a = subscribeItemEvents("rca", "I1", () => got.push("a"));
    const b = subscribeItemEvents("rca", "I1", () => got.push("b"));
    await tick();
    a();
    opened[0]!.push(changed);
    await tick();
    expect(got).toEqual(["b"]);
    b();
  });

  it("a late subscriber is handed the current presence roster at once", async () => {
    // The backend sends a roster only when a viewer joins or leaves. On a
    // connection of its own a presence listener always got the join frame; on a
    // shared one that is already open it would wait for the next join/leave.
    const { opened } = fakeStreams();
    const early = subscribeItemEvents("rca", "I1", () => {});
    await tick();
    opened[0]!.push({ type: "presence", users: ["alice", "bob"] } as AgentEvent);
    opened[0]!.push(changed);
    await tick();
    const got: AgentEvent[] = [];
    const late = subscribeItemEvents("rca", "I1", (ev) => got.push(ev));
    await tick();
    expect(got).toEqual([{ type: "presence", users: ["alice", "bob"] }]);
    early();
    late();
  });

  it("subscribing again after everyone left opens a fresh connection", async () => {
    const { spy } = fakeStreams();
    subscribeItemEvents("rca", "I1", () => {})();
    await tick();
    const again = subscribeItemEvents("rca", "I1", () => {});
    await tick();
    expect(spy).toHaveBeenCalledTimes(2);
    again();
  });
});
