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

import type { QueryClient } from "@tanstack/react-query";

import { HttpError } from "../api/http";
import { makeQueryClient } from "../api/queryClient";
import { currentWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import type { HelperJob, HelperRelease, OutsideLookupClient } from "../api/outsideLookup";
import { ChatItemProvider } from "../hooks/chatItem";
import type { OutsideLookup } from "../renderers/outsideLookup";
import { qk } from "../api/queryKeys";
import { makeTestQueryClient, renderWithQuery } from "../test/queryWrapper";

import { OutsideLookupCard } from "./OutsideLookupCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SEARCH: OutsideLookup = { why: "需要變更", query: "pandas 2.0" };
const PAGE: OutsideLookup = { why: "看那一頁", url: "https://pandas.example/whatsnew" };
const RELEASE: HelperRelease = {
  version: "0.1.0",
  min_version: "0.0.0",
  file: "outside-helper-0.1.0.zip",
  sha256: "ab",
  size: 1,
};

function job(over: Partial<HelperJob> = {}): HelperJob {
  return {
    id: "j1",
    kind: "search",
    args: "pandas 2.0",
    state: "queued",
    error: "",
    note: "",
    auto: false,
    saved: "",
    ...over,
  };
}

function draw({
  lookup = SEARCH,
  online = true,
  handOver = async () => job(),
  current,
  existing = null,
  release = RELEASE,
  queryClient,
  status,
}: {
  lookup?: OutsideLookup;
  online?: boolean;
  /** Overrides `online`: a status that changes over the test. */
  status?: () => Promise<{ online: boolean }>;
  handOver?: OutsideLookupClient["handOver"];
  /** The job as read once one exists; defaults to the last one handed over. */
  current?: (args: Parameters<NonNullable<OutsideLookupClient["job"]>>[0]) => Promise<HelperJob>;
  /** A job the card already has when it is drawn (a reload mid-errand). */
  existing?: HelperJob | null;
  release?: HelperRelease | null;
  /** The app's own client — with its "could not save" notice — when that matters. */
  queryClient?: QueryClient;
} = {}) {
  // As the server answers: no job until one is handed over, then that job.
  let latest: HelperJob | null = existing;
  const client = {
    targets: vi.fn(async () => [{ name: "Google", url: "https://www.google.com/search?q={q}" }]),
    answer: vi.fn(async () => ({ path: null, attachments: [] })),
    helperStatus: vi.fn(status ?? (async () => ({ online }))),
    handOver: vi.fn(async (args: Parameters<OutsideLookupClient["handOver"] & object>[0]) => {
      latest = await handOver(args);
      return latest;
    }),
    job: vi.fn(async (args: Parameters<NonNullable<OutsideLookupClient["job"]>>[0]) =>
      latest === null ? null : current ? current(args) : latest,
    ),
    cancel: vi.fn(async () => {}),
    release: vi.fn(async () => release),
  };
  vi.spyOn(window, "open").mockReturnValue(null);
  renderWithQuery(
    <ChatItemProvider value={{ slug: "rca", itemId: "i1", chatId: "ch1", answered: () => false }}>
      <OutsideLookupCard callId="call_1" lookup={lookup} client={client} />
    </ChatItemProvider>,
    queryClient,
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

  // §3.5: the offline line says where to get the helper — in a new tab, so
  // the chat the card is in stays where it is.
  it("links to where the helper is installed from", async () => {
    draw({ online: false });

    const link = await screen.findByRole("link", { name: "怎麼安裝" });
    expect(link).toHaveAttribute("href", "/outside-helper");
    expect(link).toHaveAttribute("target", "_blank");
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

  // §3.4: a captcha stops the helper until the person solves it in the
  // helper's own Chrome window — the card says where to look.
  it("says where to look when the helper waits on a verification", async () => {
    draw({ current: async () => job({ state: "claimed", note: "captcha" }) });
    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    expect(await screen.findByText("請在小幫手的 Chrome 視窗裡完成驗證")).toBeInTheDocument();
    expect(screen.queryByText("小幫手正在查…")).not.toBeInTheDocument();
  });
});

describe("review round 1", () => {
  // A reload mid-errand: the card reads its own job, not just one it handed
  // over in this page's life — offering 「查」 again restarted it.
  it("shows a job already under way after a reload, with no 「查」", async () => {
    draw({ existing: job({ state: "claimed" }) });

    expect(await screen.findByText("小幫手正在查…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查" })).not.toBeInTheDocument();
  });

  // D7: with nothing published there is no helper to get — the card is
  // today's, not a pointer to an empty page.
  it("says nothing of a helper when none is published", async () => {
    const client = draw({ online: false, release: null });

    expect(await screen.findByRole("button", { name: /Google/ })).toBeInTheDocument();
    await waitFor(() => expect(client.release).toHaveBeenCalled());
    expect(screen.queryByText("外網小幫手未連線")).not.toBeInTheDocument();
  });

  // §3.5 「自己查」展開區 — a disclosure: says whether it is open, and closes
  // again (GOV.UK Design System, Details; NN/g, progressive disclosure).
  it("opens and closes doing it by hand", async () => {
    draw();
    const toggle = await screen.findByRole("button", { name: "自己查" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("button", { name: /Google/ })).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /Google/ })).not.toBeInTheDocument();
  });

  // §3.5: a page opened with no nod says why.
  it("says why a page opened by itself", async () => {
    const running = job({ kind: "open_url", state: "claimed", auto: true });
    // as the server answers the card's own request for a job already running
    draw({ lookup: PAGE, existing: running, handOver: async () => running });
    expect(await screen.findByText("來自先前查到的內容")).toBeInTheDocument();
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

  // The card asking on its own and being told "not this one" is the ordinary
  // way back to the nod — not a failed save to announce app-wide (found live,
  // P10: 「儲存失敗,內容未套用。」 popped over the chat).
  it("is told to wait for the nod without an app-wide error", async () => {
    resetWriteFailures();
    draw({
      lookup: PAGE,
      queryClient: makeQueryClient(),
      handOver: async (args) => {
        if (args.auto) throw new HttpError(409, "this lookup waits for the person's 「查」");
        return job({ kind: "open_url" });
      },
    });

    expect(await screen.findByRole("button", { name: "查" })).toBeInTheDocument();
    expect(currentWriteFailure()).toBeNull();
  });

  // A helper waiting minutes on a captcha checks in with nobody — it is busy
  // holding THIS card's job. The card keeps saying where to look rather than
  // turning into "not connected, do it yourself" (review round 1).
  it("keeps showing the job a helper holds when its check-ins lapse", async () => {
    const qc = makeTestQueryClient();
    let online = true;
    draw({
      queryClient: qc,
      status: async () => ({ online }),
      current: async () => job({ state: "claimed", note: "captcha" }),
    });
    fireEvent.click(await screen.findByRole("button", { name: "查" }));
    expect(await screen.findByText("請在小幫手的 Chrome 視窗裡完成驗證")).toBeInTheDocument();

    online = false;
    await qc.invalidateQueries({ queryKey: qk.outsideHelper() });

    await waitFor(() => expect(screen.queryByText("外網小幫手未連線")).not.toBeInTheDocument());
    expect(screen.getByText("請在小幫手的 Chrome 視窗裡完成驗證")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Google/ })).not.toBeInTheDocument();
  });

  // Silent app-wide is not silent: a 「查」 the server refuses says why, here.
  it("says in the card why a 「查」 was refused", async () => {
    draw({
      queryClient: makeQueryClient(),
      handOver: async () => {
        throw new HttpError(409, "the outside helper is not connected");
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "查" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("the outside helper is not connected");
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
