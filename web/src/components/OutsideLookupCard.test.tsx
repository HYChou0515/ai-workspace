/**
 * The "請幫我查" card (docs/plan-outside-lookup.md): the AI asked the person to
 * look something up outside the air-gapped backend. Search (or open the page)
 * in a new tab, paste what was found, send — or say it could not be found.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OutsideAnswer, OutsideLookupClient } from "../api/outsideLookup";
import { HttpError } from "../api/http";
import { ChatItemProvider } from "../hooks/chatItem";
import type { OutsideLookup } from "../renderers/outsideLookup";
import { renderWithQuery } from "../test/queryWrapper";

import { OutsideLookupCard } from "./OutsideLookupCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const TARGETS = [
  { name: "Google", url: "https://www.google.com/search?q={q}" },
  { name: "Wiki", url: "http://wiki.example/?s={q}" },
];

function draw({
  lookup = { why: "需要 2.0 的變更", query: "pandas 2.0 breaking" } as OutsideLookup,
  answered = false,
  item = true,
  canAddFiles = true,
  answer = async () => ({ path: "lookups/a.md", attachments: [] }),
}: {
  lookup?: OutsideLookup;
  answered?: boolean;
  item?: boolean;
  canAddFiles?: boolean;
  answer?: (args: Parameters<OutsideLookupClient["answer"]>[0]) => Promise<{
    path: string | null;
    attachments: string[];
  }>;
} = {}) {
  const client = { targets: vi.fn(async () => TARGETS), answer: vi.fn(answer) };
  const open = vi.spyOn(window, "open").mockReturnValue(null);
  const card = <OutsideLookupCard callId="call_1" lookup={lookup} client={client} />;
  renderWithQuery(
    item ? (
      <ChatItemProvider
        value={{
          slug: "rca",
          itemId: "i1",
          chatId: "ch1",
          answered: () => answered,
          ...(canAddFiles ? {} : { canAddFiles: false as const }),
        }}
      >
        {card}
      </ChatItemProvider>
    ) : (
      card
    ),
  );
  return { client, open };
}

function sent(client: { answer: ReturnType<typeof vi.fn> }): OutsideAnswer {
  return (client.answer.mock.calls[0]![0] as { answer: OutsideAnswer }).answer;
}

describe("asking for a search", () => {
  it("says why, and offers one button per search destination", async () => {
    draw();

    expect(screen.getByText("請幫我查")).toBeInTheDocument();
    expect(screen.getByText("需要 2.0 的變更")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Google/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Wiki/ })).toBeInTheDocument();
  });

  it("says, before the click, that the buttons open a new tab (NN/g)", async () => {
    draw();

    const google = await screen.findByRole("button", { name: /Google/ });
    expect(google).toHaveAccessibleName(/新分頁/);
  });

  it("searches what the person edited the query to", async () => {
    const { open } = draw();
    fireEvent.change(screen.getByLabelText("查詢"), { target: { value: "pandas 2.1" } });

    fireEvent.click(await screen.findByRole("button", { name: /Wiki/ }));

    expect(open).toHaveBeenCalledWith("http://wiki.example/?s=pandas%202.1", "_blank", "noopener,noreferrer");
  });

  it("copies the query as edited", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    draw();
    fireEvent.change(screen.getByLabelText("查詢"), { target: { value: "edited" } });

    fireEvent.click(screen.getByRole("button", { name: "複製問題" }));

    expect(writeText).toHaveBeenCalledWith("edited");
    expect(await screen.findByRole("button", { name: "已複製" })).toBeInTheDocument();
  });
});

describe("asking to open a page", () => {
  it("shows the whole address and opens exactly it", async () => {
    const url = "https://spec.example/a?b=1#c";
    const { open } = draw({ lookup: { why: "文件連到規格", url } });

    expect(screen.getByText(url)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Google/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /開啟這個網址/ }));

    expect(open).toHaveBeenCalledWith(url, "_blank", "noopener,noreferrer");
  });
});

describe("bringing it back", () => {
  it("turns a pasted page into Markdown, links and tables kept", () => {
    draw();
    const box = screen.getByLabelText("查到的內容") as HTMLTextAreaElement;
    const html = '<p>See <a href="https://a.example">notes</a></p><script>x()</script>';

    fireEvent.paste(box, {
      clipboardData: { getData: (t: string) => (t === "text/html" ? html : "See notes") },
    });

    expect(box.value).toBe("See [notes](https://a.example)");
  });

  it("pastes plain text as it is", () => {
    draw();
    const box = screen.getByLabelText("查到的內容") as HTMLTextAreaElement;

    fireEvent.paste(box, { clipboardData: { getData: (t: string) => (t === "text/plain" ? "a  b" : "") } });

    // Not intercepted: the browser's own paste puts the text in.
    expect(box.value).toBe("");
  });

  it("sends what was found, the source, the button pressed and the files", async () => {
    const { client } = draw();
    fireEvent.click(await screen.findByRole("button", { name: /Google/ }));
    fireEvent.change(screen.getByLabelText("查到的內容"), { target: { value: "found" } });
    fireEvent.change(screen.getByLabelText("來源網址(選填)"), { target: { value: "https://s" } });
    const file = new File(["x"], "shot.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("附加檔案"), { target: { files: [file] } });
    expect(screen.getByText("shot.png")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() => expect(client.answer).toHaveBeenCalled());
    expect(client.answer.mock.calls[0]![0]).toMatchObject({
      slug: "rca",
      itemId: "i1",
      chatId: "ch1",
      callId: "call_1",
    });
    expect(sent(client)).toEqual({
      kind: "found",
      content: "found",
      sourceUrl: "https://s",
      target: "Google",
      attachments: [file],
    });
  });

  it("lets a chosen file be removed", () => {
    draw();
    const file = new File(["x"], "shot.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("附加檔案"), { target: { files: [file] } });

    fireEvent.click(screen.getByRole("button", { name: "移除 shot.png" }));

    expect(screen.queryByText("shot.png")).toBeNull();
    expect(screen.getByText("尚未選擇檔案")).toBeInTheDocument();
  });

  it("says what is missing instead of sending nothing", () => {
    const { client } = draw();

    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    expect(screen.getByRole("alert")).toHaveTextContent("貼上查到的內容,或附加檔案");
    expect(client.answer).not.toHaveBeenCalled();
  });

  it("once sent, keeps what was sent readable and takes the send away", async () => {
    draw();
    fireEvent.change(screen.getByLabelText("查到的內容"), { target: { value: "found" } });

    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    expect(await screen.findByText(/已存到 lookups\/a\.md/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "送出" })).toBeNull();
    expect(screen.queryByRole("button", { name: /查不到/ })).toBeNull();
    expect(screen.getByLabelText("查到的內容")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("查到的內容")).toHaveValue("found");
  });

  it("shows why a send was refused, and keeps everything to try again", async () => {
    draw({
      answer: async () => {
        throw new HttpError(507, "x");
      },
    });
    fireEvent.change(screen.getByLabelText("查到的內容"), { target: { value: "found" } });

    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("空間已滿");
    expect(screen.getByRole("button", { name: "送出" })).toBeInTheDocument();
    expect(screen.getByLabelText("查到的內容")).toHaveValue("found");
  });
});

describe("review round 1", () => {
  it("sends the query as edited, and the person's own date", async () => {
    const { client } = draw();
    fireEvent.change(screen.getByLabelText("查詢"), { target: { value: "pandas 2.1" } });
    fireEvent.change(screen.getByLabelText("查到的內容"), { target: { value: "x" } });

    fireEvent.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() => expect(client.answer).toHaveBeenCalled());
    const args = client.answer.mock.calls[0]![0] as { query?: string; date?: string };
    expect(args.query).toBe("pandas 2.1");
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    expect(args.date).toBe(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
  });

  it("says, for a page too, that its button opens a new tab (NN/g)", () => {
    draw({ lookup: { why: "w", url: "https://a.example" } });

    expect(screen.getByText("標 ↗ 的按鈕會在新分頁開啟")).toBeInTheDocument();
  });

  it("offers no attachments to someone who may not add files, and says the text is not saved", async () => {
    const { client } = draw({ canAddFiles: false });

    expect(screen.queryByLabelText("附加檔案")).toBeNull();
    expect(screen.getByText(/不會存成檔案/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("查到的內容"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "送出" }));
    await waitFor(() => expect(client.answer).toHaveBeenCalled());
  });
});

describe("could not find it (D7)", () => {
  it("says so, with an optional reason", async () => {
    const { client } = draw();

    fireEvent.click(screen.getByRole("button", { name: "查不到／不查了" }));
    fireEvent.change(screen.getByLabelText("原因(選填)"), { target: { value: "公司擋了" } });
    fireEvent.click(screen.getByRole("button", { name: "送出「查不到」" }));

    await waitFor(() => expect(client.answer).toHaveBeenCalled());
    expect(sent(client)).toEqual({ kind: "not_found", reason: "公司擋了" });
  });

  it("can be backed out of", () => {
    draw();
    fireEvent.click(screen.getByRole("button", { name: "查不到／不查了" }));

    fireEvent.click(screen.getByRole("button", { name: "返回" }));

    expect(screen.getByRole("button", { name: "送出" })).toBeInTheDocument();
  });
});

describe("where it cannot be answered", () => {
  it("an answered card offers nothing to send", () => {
    draw({ answered: true });

    expect(screen.getByText("已回覆")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "送出" })).toBeNull();
    expect(screen.queryByLabelText("查到的內容")).toBeNull();
  });

  it("outside an item chat it shows the request without actions", () => {
    const { client } = draw({ item: false });

    expect(screen.getByText("需要 2.0 的變更")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "送出" })).toBeNull();
    expect(client.targets).not.toHaveBeenCalled();
  });
});
