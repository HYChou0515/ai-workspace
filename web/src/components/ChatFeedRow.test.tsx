// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AgentEntry } from "../pages/investigation/agentLog";
import { QueryWrap } from "../test/queryWrapper";
import { ChatFeedRow } from "./ChatFeedRow";

afterEach(cleanup);

const msg = (role: string, content: string) =>
  ({ kind: "message", at: 1, message: { role, content } }) as unknown as AgentEntry;

describe("ChatFeedRow", () => {
  it("draws each entry in a row the browser may skip laying out off screen (chat-feed.css)", () => {
    const { container } = render(
      <ChatFeedRow entry={msg("user", "hi")} index={0} settled actions={{}} />,
      { wrapper: QueryWrap },
    );
    const row = container.firstElementChild;
    expect(row).toHaveClass("chat-feed-row");
    expect(row).not.toHaveClass("chat-feed-row--answer");
  });

  it("marks an answer, which stands in at an answer's height", () => {
    const { container } = render(
      <ChatFeedRow entry={msg("assistant", "long answer")} index={1} settled actions={{}} />,
      { wrapper: QueryWrap },
    );
    expect(container.firstElementChild).toHaveClass("chat-feed-row", "chat-feed-row--answer");
  });
});
