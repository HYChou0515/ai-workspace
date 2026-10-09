/**
 * A slide deck in the file viewer (docs/plan-pptx-preview.md N3, N6, D6): the
 * server converts it to a PDF and the browser's PDF viewer shows it. A big deck
 * is converted only after the person says yes; a failure says why and offers
 * the original.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  FileServiceProvider,
  type FileService,
  type SlidePreview,
} from "../api/fileService";
import { qk } from "../api/queryKeys";
import { DialogProvider } from "../components/Dialog";
import { publishFileChanged } from "../lib/fileChangedBus";
import { renderWithQuery } from "../test/queryWrapper";
import { hasEditToggle, pickRenderer } from "./registry";
import { SlidesRenderer } from "./SlidesRenderer";

beforeAll(() => {
  URL.createObjectURL = vi.fn(() => "blob:deck");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);

function draw(
  answer: (confirm: boolean) => SlidePreview,
  path = "/slides/q3.pptx",
) {
  const slidePreview = vi.fn(async (_path: string, confirm: boolean) =>
    answer(confirm),
  );
  const svc = {
    scopeId: "i1",
    slidePreview,
    fileDownloadUrl: (p: string) => `/api/files${p}`,
  } as unknown as FileService;
  const view = (at: string) => (
    <FileServiceProvider value={svc}>
      <SlidesRenderer path={at} />
    </FileServiceProvider>
  );
  const { client, rerender } = renderWithQuery(view(path));
  return Object.assign(slidePreview, {
    client,
    // The same tree `renderWithQuery` drew, so React keeps the component (and
    // its state) — a different root would remount it and prove nothing.
    show: (at: string) =>
      rerender(
        <QueryClientProvider client={client}>
          <DialogProvider>{view(at)}</DialogProvider>
        </QueryClientProvider>,
      ),
  });
}

const pdf: SlidePreview = {
  kind: "pdf",
  blob: new Blob(["%PDF"], { type: "application/pdf" }),
};

describe("SlidesRenderer", () => {
  it("shows the converted deck in the browser's PDF viewer", async () => {
    draw(() => pdf);

    const frame = await screen.findByTitle("slides/q3.pptx");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "blob:deck");
  });

  it("asks before converting a big deck, and converts once told yes", async () => {
    // Once converted, the server serves its cache without asking.
    let converted = false;
    const ask = draw((confirm) => {
      if (confirm) converted = true;
      return converted
        ? pdf
        : { kind: "confirm", size: 42 * 1024 * 1024, limit: 20 * 1024 * 1024 };
    });

    expect(await screen.findByText(/42 MB/)).toBeInTheDocument();
    expect(screen.queryByTitle("slides/q3.pptx")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /預覽|Preview/ }));

    expect(await screen.findByTitle("slides/q3.pptx")).toBeInTheDocument();
    expect(ask).toHaveBeenCalledWith("/slides/q3.pptx", true);
  });

  it("says the deck could not be converted, and offers the original", async () => {
    draw(() => ({ kind: "failed", why: "source file could not be loaded" }));

    expect(
      await screen.findByText(/無法預覽這份簡報|Can't preview this deck/),
    ).toBeInTheDocument();
    // The converter's own words are for the logs, not the person (no internals).
    expect(screen.queryByText(/source file could not be loaded/)).toBeNull();
    expect(screen.getByRole("link", { name: /下載|Download/ })).toHaveAttribute(
      "href",
      "/api/files/slides/q3.pptx",
    );
  });

  it("says plainly when the preview could not be fetched, without the error's text", async () => {
    draw(() => {
      throw new Error("HTTP 503 sandbox_gone");
    });

    expect(
      await screen.findByText(/暫時無法預覽|can't be previewed right now/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/sandbox_gone/)).toBeNull();
    expect(
      screen.getByRole("link", { name: /下載|Download/ }),
    ).toBeInTheDocument();
  });

  it("says the sandbox limit is reached, with the numbers, when converting would open one", async () => {
    draw(() => ({
      kind: "refused",
      detail: {
        error: "sandbox_quota_exceeded",
        dimension: "sandboxes",
        used: 2,
        limit: 2,
      },
    }));

    expect(
      await screen.findByText(/沙盒已達上限|limit for live sandboxes/),
    ).toBeInTheDocument();
    expect(screen.getByText(/2/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /下載|Download/ }),
    ).toBeInTheDocument();
  });

  it("asks again for the next big deck — a yes is for the deck it was given to", async () => {
    const big: SlidePreview = {
      kind: "confirm",
      size: 42 * 1024 * 1024,
      limit: 1,
    };
    const ask = draw((confirm) => (confirm ? pdf : big));
    await screen.findByText(/42 MB/);
    fireEvent.click(screen.getByRole("button", { name: /預覽|Preview/ }));
    await screen.findByTitle("slides/q3.pptx");

    ask.show("/slides/q4.pptx");

    expect(await screen.findByText(/42 MB/)).toBeInTheDocument();
    expect(ask).toHaveBeenLastCalledWith("/slides/q4.pptx", false);
  });

  it("keeps showing the deck while it re-asks without the yes", async () => {
    // After the conversion, the next ask (no yes) never answers: what is on
    // screen meanwhile is what the cache gave it — the PDF, not the question.
    let converted = false;
    const ask = draw((confirm) => {
      if (confirm) converted = true;
      if (converted && !confirm) return new Promise<never>(() => {}) as never;
      return converted
        ? pdf
        : { kind: "confirm", size: 42 * 1024 * 1024, limit: 1 };
    });
    await screen.findByText(/42 MB/);
    fireEvent.click(screen.getByRole("button", { name: /預覽|Preview/ }));

    await waitFor(() =>
      expect(ask).toHaveBeenLastCalledWith("/slides/q3.pptx", false),
    );
    expect(screen.getByTitle("slides/q3.pptx")).toBeInTheDocument();
    expect(screen.queryByText(/42 MB/)).toBeNull();
  });

  it("asks again when a big deck it said yes to is replaced by new content", async () => {
    // The server's side: a converted version is cached and served without
    // asking; new content has no cache, so a big one is asked about again.
    let converted = false;
    const big: SlidePreview = {
      kind: "confirm",
      size: 42 * 1024 * 1024,
      limit: 1,
    };
    const ask = draw((confirm) => {
      if (confirm) converted = true;
      return converted ? pdf : big;
    });
    await screen.findByText(/42 MB/);
    fireEvent.click(screen.getByRole("button", { name: /預覽|Preview/ }));
    await screen.findByTitle("slides/q3.pptx");

    // The yes is spent once the PDF is here: the next ask goes without it.
    await waitFor(() =>
      expect(ask).toHaveBeenLastCalledWith("/slides/q3.pptx", false),
    );
    converted = false;
    act(() => publishFileChanged("i1", "slides/q3.pptx"));

    expect(await screen.findByText(/42 MB/)).toBeInTheDocument();
    expect(ask).toHaveBeenLastCalledWith("/slides/q3.pptx", false);
  });

  it("asks again when the deck's file is refreshed — a turn or a refresh changed it", async () => {
    const ask = draw(() => pdf);
    await screen.findByTitle("slides/q3.pptx");
    const before = ask.mock.calls.length;

    await act(() =>
      ask.client.invalidateQueries({
        queryKey: qk.file("i1", "/slides/q3.pptx"),
      }),
    );

    await waitFor(() => expect(ask.mock.calls.length).toBe(before + 1));
  });

  it("asks again when someone saves the deck", async () => {
    const ask = draw(() => pdf);
    await screen.findByTitle("slides/q3.pptx");
    const before = ask.mock.calls.length;

    act(() => publishFileChanged("i1", "slides/q3.pptx"));

    await waitFor(() => expect(ask.mock.calls.length).toBe(before + 1));
  });

  it("is not asked again when another file is saved", async () => {
    const ask = draw(() => pdf);
    await screen.findByTitle("slides/q3.pptx");
    const before = ask.mock.calls.length;

    act(() => publishFileChanged("i1", "notes.md"));
    await new Promise((r) => setTimeout(r, 20));

    expect(ask.mock.calls.length).toBe(before);
  });
});

// Every state that is not the PDF is one centred block (Polaris "Empty state"):
// a heading that says what is going on, a line of detail, and the actions as
// real buttons — the main one filled, "download the original" outlined.
describe("SlidesRenderer — the states around the PDF", () => {
  const downloadIsAButton = () => {
    const link = screen.getByRole("link", {
      name: /下載原檔|Download the original/,
    });
    expect(link).toHaveAttribute("href", "/api/files/slides/q3.pptx");
    expect(link).toHaveClass("btn");
    expect(link).toHaveAttribute("data-variant", "secondary");
    expect(link).toHaveAttribute("data-size", "md");
  };

  it("asks about a big deck with a heading, a filled action and an outlined download", async () => {
    draw(() => ({ kind: "confirm", size: 42 * 1024 * 1024, limit: 1 }));

    expect(
      await screen.findByRole("heading", { name: /42 MB/ }),
    ).toBeInTheDocument();
    const go = screen.getByRole("button", {
      name: /轉成預覽|Convert to preview/,
    });
    expect(go).toHaveAttribute("data-variant", "primary");
    expect(go).toHaveAttribute("data-size", "md");
    downloadIsAButton();
  });

  it("shows progress while converting, and the original is there meanwhile", async () => {
    draw(() => new Promise<never>(() => {}) as never);

    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /正在轉成預覽|Converting/ }),
    ).toBeInTheDocument();
    downloadIsAButton();
  });

  it("says a deck could not be converted as a heading, with the download as a button", async () => {
    draw(() => ({ kind: "failed", why: "x" }));

    expect(
      await screen.findByRole("heading", { name: /無法預覽|Can't preview/ }),
    ).toBeInTheDocument();
    downloadIsAButton();
  });

  it("says the preview could not be fetched as a heading, with the download as a button", async () => {
    draw(() => {
      throw new Error("HTTP 503");
    });

    expect(
      await screen.findByRole("heading", { name: /暫時無法預覽|right now/ }),
    ).toBeInTheDocument();
    downloadIsAButton();
  });

  it("says the sandbox limit is reached as a heading, the numbers below", async () => {
    draw(() => ({
      kind: "refused",
      detail: {
        error: "sandbox_quota_exceeded",
        dimension: "sandboxes",
        used: 2,
        limit: 2,
      },
    }));

    expect(
      await screen.findByRole("heading", { name: /暫時無法預覽|right now/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/沙盒已達上限|limit for live sandboxes/),
    ).toBeInTheDocument();
    downloadIsAButton();
  });

  it("says where a deck can't be previewed, with the download as a button", () => {
    const svc = {
      scopeId: "k1",
      fileDownloadUrl: (p: string) => `/api/files${p}`,
    } as unknown as FileService;
    renderWithQuery(
      <FileServiceProvider value={svc}>
        <SlidesRenderer path="/slides/q3.pptx" />
      </FileServiceProvider>,
    );

    expect(
      screen.getByRole("heading", {
        name: /無法預覽簡報|can't be previewed here/,
      }),
    ).toBeInTheDocument();
    downloadIsAButton();
  });
});

describe("the slide renderer in the registry", () => {
  it.each(["/a/q3.pptx", "/a/Q3.PPT", "/a/q3.odp"])("previews %s", (path) => {
    expect(pickRenderer(path)).toBe("slides");
  });

  it("keeps the Edit toggle — editing shows the file as it is today", () => {
    expect(hasEditToggle("slides")).toBe(true);
  });
});
