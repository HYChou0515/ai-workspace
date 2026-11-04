// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AgentEntry } from "../pages/investigation/agentLog";
import { QueryWrap } from "../test/queryWrapper";
import { ChatFeedRow } from "./ChatFeedRow";

afterEach(cleanup);

const answer = (content: string) =>
  ({ kind: "message", at: 1, message: { role: "assistant", content } }) as unknown as AgentEntry;

describe("ChatFeedRow", () => {
  it("resolves an answer's images against the item it is drawn for now", () => {
    // `fileUrl` is called while the markdown is drawn, not when something is
    // clicked, so it cannot be one of the row's identity-stable actions: a
    // row kept for another item would keep the first item's file URLs
    // (review: the proxy's identity never changes, and the memo'd body
    // compared equal on everything else).
    const fileUrl = (item: string) => (p: string) => `/api/${item}/files/${p}`;
    const view = render(
      <ChatFeedRow entry={answer("![c](out/c.png)")} index={0} settled actions={{}} fileUrl={fileUrl("A")} />,
      { wrapper: QueryWrap },
    );
    expect(view.container.querySelector("img")).toHaveAttribute("src", "/api/A/files/out/c.png");

    view.rerender(
      <ChatFeedRow entry={answer("![c](out/c.png)")} index={0} settled actions={{}} fileUrl={fileUrl("B")} />,
    );

    expect(view.container.querySelector("img")).toHaveAttribute("src", "/api/B/files/out/c.png");
  });
});
