/**
 * The 問外部AI card (docs/plan-ext-ai.md §3): the AI drafted a question; the
 * person reads and edits it, and presses 「問外部AI」 — their helper asks the
 * company's web AI. A helper that cannot (none, or no site file packed in)
 * leaves the person to ask it themselves and paste the answer back (D9).
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HelperJob, OutsideLookupClient } from "../api/outsideLookup";
import { ChatItemProvider } from "../hooks/chatItem";
import type { OutsideLookup } from "../renderers/outsideLookup";
import { renderWithQuery } from "../test/queryWrapper";

import { OutsideLookupCard } from "./OutsideLookupCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const QUESTION = "Which is safer for a 2 GB CSV, pandas or polars?";
const ASK: OutsideLookup = { why: "比較兩個做法", question: QUESTION };

function job(over: Partial<HelperJob> = {}): HelperJob {
  return {
    id: "j1",
    kind: "ask_ext_ai",
    args: QUESTION,
    state: "queued",
    error: "",
    note: "",
    auto: false,
    saved: "",
    ...over,
  };
}

function draw({
  online = true,
  extAi = true,
  current,
  existing = null,
}: {
  online?: boolean;
  extAi?: boolean;
  current?: () => Promise<HelperJob>;
  /** A job the card already has when it is drawn. */
  existing?: HelperJob | null;
} = {}) {
  let latest: HelperJob | null = existing;
  const client = {
    targets: vi.fn(async () => [{ name: "Google", url: "https://www.google.com/search?q={q}" }]),
    answer: vi.fn(async () => ({ path: "lookups/x.md", attachments: [] })),
    helperStatus: vi.fn(async () => ({ online, ext_ai: online && extAi })),
    handOver: vi.fn(async (args: Parameters<NonNullable<OutsideLookupClient["handOver"]>>[0]) => {
      latest = job({ args: args.query ?? "" });
      return latest;
    }),
    job: vi.fn(async () => (latest === null ? null : current ? current() : latest)),
    cancel: vi.fn(async () => {}),
    release: vi.fn(async () => null),
  };
  renderWithQuery(
    <ChatItemProvider value={{ slug: "rca", itemId: "i1", chatId: "ch1" }}>
      <OutsideLookupCard callId="call_1" lookup={ASK} client={client} />
    </ChatItemProvider>,
  );
  return client;
}

describe("asking the external AI", () => {
  it("shows the AI's question, editable, under its own title", async () => {
    draw();

    expect(screen.getByText("問外部AI", { selector: ".outside-lookup-title" })).toBeInTheDocument();
    expect(screen.getByText("比較兩個做法")).toBeInTheDocument();
    expect(screen.getByLabelText("要問的問題")).toHaveValue(QUESTION);
  });

  // D2: what goes out is what the person left in the box.
  it("hands the question as the person edited it to their helper", async () => {
    const client = draw();
    fireEvent.change(screen.getByLabelText("要問的問題"), { target: { value: "edited?" } });

    fireEvent.click(await screen.findByRole("button", { name: "問外部AI" }));

    await waitFor(() =>
      expect(client.handOver).toHaveBeenCalledWith(
        expect.objectContaining({ callId: "call_1", query: "edited?", auto: false }),
      ),
    );
  });

  it("with the box emptied, asks nothing and says to write the question", async () => {
    const client = draw();
    fireEvent.change(screen.getByLabelText("要問的問題"), { target: { value: "  " } });

    fireEvent.click(await screen.findByRole("button", { name: "問外部AI" }));

    expect(screen.getByText("先在框裡寫好要問的問題")).toBeInTheDocument();
    expect(screen.getByLabelText("要問的問題")).toHaveFocus();
    expect(client.handOver).not.toHaveBeenCalled();
  });

  // NN/g, progress indicators: past 10 s, say what is happening and how long,
  // and let the person stop it.
  it("says it is asking, for how long, and can be cancelled", async () => {
    const client = draw();
    fireEvent.click(await screen.findByRole("button", { name: "問外部AI" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("小幫手正在問外部AI");
    expect(screen.getByText(/已等 \d+ 秒/)).toBeInTheDocument();
    // …but outside the live region: a polite region whose text changes every
    // second is read out every second (review round 1)
    expect(status).not.toHaveTextContent(/已等/);
    // what was sent is what the box shows: no editing a question already out
    expect(screen.getByLabelText("要問的問題")).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(client.cancel).toHaveBeenCalled());
  });

  // D4: the external AI's page may first ask the person to sign in, in the
  // helper's Chrome window — the card says where.
  it("says where to sign in when the helper waits on it", async () => {
    draw({ current: async () => job({ state: "claimed", note: "login" }) });
    fireEvent.click(await screen.findByRole("button", { name: "問外部AI" }));

    expect(await screen.findByText("請在小幫手的 Chrome 視窗裡登入外部AI")).toBeInTheDocument();
    expect(screen.queryByText("小幫手正在問外部AI…")).not.toBeInTheDocument();
  });

  it("says why it failed, and asks again with the question kept", async () => {
    const client = draw({ current: async () => job({ state: "failed", error: "沒有回應" }) });
    fireEvent.change(screen.getByLabelText("要問的問題"), { target: { value: "kept?" } });
    fireEvent.click(await screen.findByRole("button", { name: "問外部AI" }));

    expect(await screen.findByText("小幫手沒問到:沒有回應")).toBeInTheDocument();
    expect(screen.getByLabelText("要問的問題")).toHaveValue("kept?");
    fireEvent.click(screen.getByRole("button", { name: "再問外部AI 一次" }));

    await waitFor(() => expect(client.handOver).toHaveBeenCalledTimes(2));
  });
});

describe("asking it by hand (D9)", () => {
  it("a helper with no external AI set up says so, and the card is the manual one", async () => {
    const client = draw({ extAi: false });

    expect(await screen.findByText("你的小幫手沒有設定外部AI,請自己去問,把回答貼回來")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "問外部AI" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "複製問題" })).toBeInTheDocument();
    expect(client.handOver).not.toHaveBeenCalled();
  });

  it("copies the question as it stands in the box", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    draw({ online: false });
    fireEvent.change(screen.getByLabelText("要問的問題"), { target: { value: "edited?" } });

    fireEvent.click(screen.getByRole("button", { name: "複製問題" }));

    expect(writeText).toHaveBeenCalledWith("edited?");
    expect(await screen.findByRole("button", { name: "已複製" })).toBeInTheDocument();
  });

  it("sends the pasted answer with the question that was asked", async () => {
    const client = draw({ online: false });
    fireEvent.change(screen.getByLabelText("要問的問題"), { target: { value: "edited?" } });
    fireEvent.change(screen.getByLabelText("外部AI 的回答"), { target: { value: "Polars." } });

    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() =>
      expect(client.answer).toHaveBeenCalledWith(
        expect.objectContaining({
          query: "edited?",
          answer: expect.objectContaining({ kind: "found", content: "Polars." }),
        }),
      ),
    );
  });

  it("has no source-address field: the answer's source is the external AI", async () => {
    draw({ online: false });

    expect(screen.queryByLabelText("來源網址(選填)")).not.toBeInTheDocument();
  });

  it("can say it was not asked", async () => {
    const client = draw({ online: false });

    fireEvent.click(screen.getByRole("button", { name: "沒問到／不問了" }));
    fireEvent.click(screen.getByRole("button", { name: "送出「沒問到」" }));

    await waitFor(() =>
      expect(client.answer).toHaveBeenCalledWith(
        expect.objectContaining({ answer: expect.objectContaining({ kind: "not_found" }) }),
      ),
    );
  });
});

// Review round 2: read-only only while the helper is at it — a job left
// waiting by a helper that went away leaves the person to ask by hand, and
// they may want to change the question first.
describe("a job left waiting, the helper gone", () => {
  it("is the manual card, with the question editable", async () => {
    const client = draw({ online: false, existing: job({ state: "queued" }) });
    // the card has read its job
    await waitFor(() => expect(client.job).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));

    expect(screen.getByRole("button", { name: "複製問題" })).toBeInTheDocument();
    expect(screen.getByLabelText("要問的問題")).not.toHaveAttribute("readonly");
  });
});
