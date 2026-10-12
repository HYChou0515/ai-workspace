/**
 * The card with the person's outside helper (docs/plan-outside-helper.md §3.5):
 * a Python program on their PC fetches the page, so the card asks only for the
 * nod a search needs (D4) and shows how the errand is going. With no helper
 * connected the card is exactly today's manual one (D7).
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import type { HelperJob, OutsideLookupClient } from "../api/outsideLookup";
import { ChatItemProvider } from "../hooks/chatItem";
import type { OutsideLookup } from "../renderers/outsideLookup";
import { renderWithQuery } from "../test/queryWrapper";

import { OutsideLookupCard } from "./OutsideLookupCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SEARCH: OutsideLookup = { why: "需要變更", query: "pandas 2.0" };
const PAGE: OutsideLookup = { why: "看那一頁", url: "https://pandas.example/whatsnew" };

function job(over: Partial<HelperJob> = {}): HelperJob {
  return { id: "j1", kind: "search", args: "pandas 2.0", state: "queued", error: "", ...over };
}

function draw({
  lookup = SEARCH,
  online = true,
  handOver = async () => job(),
  current = async () => job(),
}: {
  lookup?: OutsideLookup;
  online?: boolean;
  handOver?: OutsideLookupClient["handOver"];
  current?: OutsideLookupClient["job"];
} = {}) {
  const client = {
    targets: vi.fn(async () => [{ name: "Google", url: "https://www.google.com/search?q={q}" }]),
    answer: vi.fn(async () => ({ path: null, attachments: [] })),
    helperStatus: vi.fn(async () => ({ online })),
    handOver: vi.fn(handOver),
    job: vi.fn(current),
    cancel: vi.fn(async () => {}),
  };
  vi.spyOn(window, "open").mockReturnValue(null);
  renderWithQuery(
    <ChatItemProvider value={{ slug: "rca", itemId: "i1", chatId: "ch1", answered: () => false }}>
      <OutsideLookupCard callId="call_1" lookup={lookup} client={client} />
    </ChatItemProvider>,
  );
  return client;
}

describe("with no helper connected", () => {
  it("is today's card, and says the helper is not connected", async () => {
    const client = draw({ online: false });

    expect(await screen.findByText("外網小幫手未連線")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Google/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查" })).not.toBeInTheDocument();
    expect(client.handOver).not.toHaveBeenCalled();
  });
});

describe("a search, with the helper connected", () => {
  it("asks only for the nod; doing it by hand is one step away", async () => {
    draw();

    expect(await screen.findByRole("button", { name: "查" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Google/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "自己查" }));

    expect(await screen.findByRole("button", { name: /Google/ })).toBeInTheDocument();
  });

  it("hands over the query as the person edited it, then shows it under way", async () => {
    const client = draw();
    const box = await screen.findByLabelText("查詢");
    fireEvent.change(box, { target: { value: "pandas 2.0 breaking" } });

    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    await waitFor(() =>
      expect(client.handOver).toHaveBeenCalledWith(
        expect.objectContaining({ callId: "call_1", query: "pandas 2.0 breaking", auto: false }),
      ),
    );
    expect(await screen.findByText("小幫手正在查…")).toBeInTheDocument();
  });

  it("can be cancelled while under way", async () => {
    const client = draw();
    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    fireEvent.click(await screen.findByRole("button", { name: "取消" }));

    await waitFor(() => expect(client.cancel).toHaveBeenCalled());
  });

  it("says why it failed, and can try again", async () => {
    const client = draw({ current: async () => job({ state: "failed", error: "page timed out" }) });
    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    expect(await screen.findByText("小幫手沒查到:page timed out")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重查" }));

    await waitFor(() => expect(client.handOver).toHaveBeenCalledTimes(2));
  });

  it("says the helper sent it once it is done", async () => {
    draw({ current: async () => job({ state: "done" }) });
    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    expect(await screen.findByText("小幫手已送給 AI")).toBeInTheDocument();
  });
});

describe("a page, with the helper connected (D12)", () => {
  it("opens one an earlier lookup brought back, with no nod", async () => {
    const client = draw({ lookup: PAGE, current: async () => job({ kind: "open_url" }) });

    await waitFor(() =>
      expect(client.handOver).toHaveBeenCalledWith(expect.objectContaining({ auto: true })),
    );
    expect(await screen.findByText("小幫手正在開…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查" })).not.toBeInTheDocument();
  });

  it("waits for the nod for one no lookup brought back", async () => {
    const client = draw({
      lookup: PAGE,
      handOver: async (args) => {
        if (args.auto) throw new HttpError(409, "this lookup waits for the person's 「查」");
        return job({ kind: "open_url" });
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    await waitFor(() =>
      expect(client.handOver).toHaveBeenLastCalledWith(expect.objectContaining({ auto: false })),
    );
  });
});
