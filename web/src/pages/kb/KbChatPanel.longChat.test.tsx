// @vitest-environment happy-dom
/**
 * The KB chat draws the same feed as the workspace chat, so it had the same
 * cost: every streamed delta re-parsed every finished answer's markdown (the
 * long-chat perf fix). A delta must re-parse only the answer it extends.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const parsed: string[] = [];
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: ReactNode }) => {
    parsed.push(String(children));
    return <div>{children}</div>;
  },
}));

import type { KbApi } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { KbChatPanel } from "./KbChatPanel";

beforeEach(() => {
  parsed.length = 0;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("a streamed token in a long KB chat", () => {
  it("re-parses only the answer being streamed, not the finished ones", async () => {
    const messages: { role: string; content: string; author?: string }[] = [];
    for (let i = 0; i < 20; i++) {
      messages.push({ role: "user", content: `q${i}`, author: "default-user" });
      messages.push({ role: "assistant", content: `finished answer ${i}` });
    }
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const client: KbApi = {
      ...mockKbApi,
      listCollections: async () => [],
      listChats: async () => [],
      getChat: async () =>
        ({
          resource_id: "c-1",
          title: "",
          collection_ids: [],
          owner: "default-user",
          shared_with: [],
          messages,
        }) as never,
      subscribeChat: vi.fn(() =>
        (async function* () {
          yield { type: "user_message", author: "default-user", content: "now", created_at: 1 } as never;
          yield { type: "message_delta", text: "par" } as never;
          await gate;
          yield { type: "message_delta", text: "tial" } as never;
          await new Promise<void>(() => {}); // the turn is still streaming
        })(),
      ),
    };

    render(<KbChatPanel chatId="c-1" collectionIds={["k"]} hideCollectionPicker client={client} />, {
      wrapper: QueryWrap,
    });
    await screen.findByText("finished answer 19");
    await waitFor(() => expect(parsed).toContain("par"));

    parsed.length = 0;
    release();
    // The second delta is drawn (however the reducer joins it to the first).
    await waitFor(() => expect(parsed.some((t) => t.endsWith("tial"))).toBe(true));

    expect(parsed.filter((t) => t.startsWith("finished answer"))).toEqual([]);
  });
});
