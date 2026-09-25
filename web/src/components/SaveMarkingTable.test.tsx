// @vitest-environment happy-dom
/**
 * P7 (plan-view-plugins-pr5-finish): "save as table" on a marking's header
 * control and on its sent chip. The save POSTs to the item's
 * `/markings/table` route, which selects the lit rows in the sandbox and
 * writes them as a new CSV; the control then says where it went (a link that
 * opens it) or why it could not.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { API_PREFIX } from "../api/http";
import { makeQueryClient } from "../api/queryClient";
import { qk } from "../api/queryKeys";
import type { SentMarking } from "../api/types";
import { OpenFileProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import { MarkingProvider } from "../hooks/useMarking";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { LocaleProvider, setStoredLocale } from "../lib/i18n";
import { MarkingStore } from "../lib/markings";
import { currentWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import { MarkingControl } from "../renderers/entity/MarkingControl";
import { makeTestQueryClient, QueryWrap } from "../test/queryWrapper";
import { MarkingChips } from "./MarkingChips";

const URL = `${API_PREFIX}/a/pm/items/i1/markings/table`;

function reply(body: unknown, status = 200) {
  return vi.fn(async (url: string) =>
    url === URL
      ? new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
      : new Response("{}", { status: 404 }),
  );
}

function Shell({
  children,
  store = new MarkingStore(),
  openFile = vi.fn(),
  client = makeTestQueryClient(),
}: {
  children: ReactNode;
  store?: MarkingStore;
  openFile?: (path: string) => void;
  client?: import("@tanstack/react-query").QueryClient;
}) {
  return (
    <QueryWrap client={client}>
      <LocaleProvider>
      <WorkspaceSlugProvider value="pm">
        <FileServiceProvider value={investigationFileService("pm", "i1")}>
          <MarkingProvider store={store}>
            <OpenFileProvider value={openFile}>
              <WorkspaceVisibleProvider value>{children}</WorkspaceVisibleProvider>
            </OpenFileProvider>
          </MarkingProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
      </LocaleProvider>
    </QueryWrap>
  );
}

const sent: SentMarking = {
  name: "fail",
  path: "/.markings/fail.json",
  counts: { lot: 3 },
  source: "/views/c.ai.yaml",
  error: null,
  digest: "d1g3st",
};

beforeEach(() => {
  setStoredLocale("en");
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 25, 14, 7)); // local time: the saver's clock
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function body(fetchMock: ReturnType<typeof vi.fn>) {
  const [, init] = fetchMock.mock.calls.find(([u]) => u === URL) as unknown as [string, RequestInit];
  expect(init.method).toBe("POST");
  return JSON.parse(String(init.body));
}

describe("save as table — the sent chip", () => {
  it("saves from the marking's recorded view, then links the new file", async () => {
    const fetchMock = reply({ path: "/markings/fail-20260925-1407.csv", rows: 3 });
    vi.stubGlobal("fetch", fetchMock);
    const openFile = vi.fn();
    render(
      <Shell openFile={openFile}>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    const link = await screen.findByRole("button", { name: /fail-20260925-1407\.csv/ });
    expect(screen.getByText(/Saved 3 rows/)).toBeInTheDocument();
    // The chip holds no values: the route reads the file the send wrote.
    expect(body(fetchMock)).toEqual({
      name: "fail",
      view: "/views/c.ai.yaml",
      columns: null,
      stamp: "20260925-1407",
      // What THIS message sent: the route refuses when the file has changed since.
      digest: "d1g3st",
    });
    fireEvent.click(link);
    expect(openFile).toHaveBeenCalledWith("/markings/fail-20260925-1407.csv");
  });

  it("sits beside the chip, not inside it: the pill keeps its one-line label", async () => {
    // The 1440 demo frame: inside the pill, "Saved 12 rows → …" squeezed the
    // counts onto two lines and turned the pill into a blob.
    vi.stubGlobal("fetch", reply({ path: "/markings/fail-20260925-1407.csv", rows: 3 }));
    render(
      <Shell>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );
    const pill = screen.getByTestId("marking-chip");
    expect(within(pill).queryByRole("button", { name: "Save as table" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    await screen.findByText(/Saved 3 rows/);
    expect(within(pill).queryByText(/Saved 3 rows/)).toBeNull();
  });

  it("is disabled, with its reason, when no view was recorded", () => {
    const fetchMock = reply({});
    vi.stubGlobal("fetch", fetchMock);
    render(
      <Shell>
        <MarkingChips markings={[{ ...sent, source: null }]} />
      </Shell>,
    );

    const save = screen.getByRole("button", { name: "Save as table" });
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("title", "No view is recorded for this marking");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is not offered on a chip whose marking was never written, nor in the composer", () => {
    render(
      <Shell>
        <MarkingChips markings={[{ ...sent, path: "", error: "workspace is full" }]} />
        <MarkingChips markings={[sent]} onRemove={() => {}} />
      </Shell>,
    );
    expect(screen.queryByRole("button", { name: "Save as table" })).toBeNull();
  });

  it("shows the route's refusal on the chip, in its words", async () => {
    vi.stubGlobal("fetch", reply({ detail: "workspace is full: 100 of 100 bytes used" }, 507));
    render(
      <Shell>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("workspace is full: 100 of 100 bytes used");
  });

  it("reports a refusal ONCE — on the control, not also as the global write-failure toast", async () => {
    // The live check (two sends, save from the older chip): the app's real
    // query client toasted "Couldn't save — the change was not applied" under
    // the chip's own sentence. The control shows its refusal itself.
    resetWriteFailures();
    vi.stubGlobal("fetch", reply({ detail: "fail has changed since this message was sent" }, 409));
    render(
      <Shell client={makeQueryClient()}>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("fail has changed");
    expect(currentWriteFailure()).toBeNull();
  });

  it("a refusal with no sentence still says it failed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 502 })));
    render(
      <Shell>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The table could not be saved");
  });

  it("refreshes the file tree so the new file shows", async () => {
    vi.stubGlobal("fetch", reply({ path: "/markings/fail-20260925-1407.csv", rows: 3 }));
    const client = makeTestQueryClient();
    const spy = vi.spyOn(client, "invalidateQueries");
    render(
      <Shell client={client}>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    await screen.findByText(/Saved 3 rows/);
    expect(spy).toHaveBeenCalledWith({ queryKey: qk.files("i1") });
  });
});

describe("save as table — the header control", () => {
  it("sends the marking's values and the view it sits in", async () => {
    const fetchMock = reply({ path: "/markings/fail-20260925-1407.csv", rows: 2 });
    vi.stubGlobal("fetch", fetchMock);
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["C", "A"]) }, "/views/other.ai.yaml");
    render(
      <Shell store={store}>
        <MarkingControl value="fail" onChange={() => {}} path="/views/c.ai.yaml" />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    await screen.findByText(/Saved 2 rows/);
    // The view the action was taken in — not the one that last wrote it.
    expect(body(fetchMock)).toEqual({
      name: "fail",
      view: "/views/c.ai.yaml",
      columns: { lot: ["A", "C"] },
      stamp: "20260925-1407",
      digest: null,
    });
  });

  it("wraps below the marking picker instead of squeezing it (390 px)", () => {
    // The 390 demo frame: in one unwrapped row, "Saved … → file" squeezed the
    // marking <select> to its arrow and pushed the button past the edge.
    render(
      <Shell>
        <MarkingControl value="fail" onChange={() => {}} path="/views/c.ai.yaml" />
      </Shell>,
    );
    const picker = screen.getByRole("combobox", { name: "Marking" });
    expect(picker.parentElement).toHaveStyle({ flexWrap: "wrap", maxWidth: "100%" });
    expect(picker).toHaveStyle({ flexShrink: "0" });
  });

  it("fits a box narrower than its one-line label, its label wrapping (P28)", () => {
    // measured at 390 wide, three panes side by side: the button is 115 px on
    // one line, the marking box 72-77 px, and the button ended past the
    // panel's edge (143 in a panel ending at 113)
    render(
      <Shell>
        <MarkingControl value="fail" onChange={() => {}} path="/views/c.ai.yaml" />
      </Shell>,
    );
    const save = screen.getByRole("button", { name: "Save as table" });
    expect(save).toHaveStyle({ maxWidth: "100%", whiteSpace: "normal" });
    // and grows to its wrapped lines: at the small size's fixed 28 px the
    // lines spilled over the text above and below it (seen in Chromium)
    expect(save).toHaveStyle({ height: "auto", minHeight: "28px", padding: "2px 10px" });
    expect(save.parentElement).toHaveStyle({ maxWidth: "100%" });
  });

  it("is disabled while nothing is marked", () => {
    render(
      <Shell>
        <MarkingControl value="fail" onChange={() => {}} path="/views/c.ai.yaml" />
      </Shell>,
    );
    const save = screen.getByRole("button", { name: "Save as table" });
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("title", "Nothing is marked yet");
  });

  it("is disabled for a view that is not a file", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["A"]) }, null);
    render(
      <Shell store={store}>
        <MarkingControl value="fail" onChange={() => {}} />
      </Shell>,
    );
    expect(screen.getByRole("button", { name: "Save as table" })).toHaveAttribute(
      "title",
      "This view is not a file in the workspace",
    );
  });

  it("is not offered while the view is on no marking", () => {
    render(
      <Shell>
        <MarkingControl value={null} onChange={() => {}} path="/views/c.ai.yaml" />
      </Shell>,
    );
    expect(screen.queryByRole("button", { name: "Save as table" })).toBeNull();
  });

  it("needs an item workspace: outside one it is not offered", () => {
    render(
      <QueryWrap>
        <MarkingControl value="fail" onChange={() => {}} path="/views/c.ai.yaml" />
      </QueryWrap>,
    );
    expect(screen.queryByRole("button", { name: "Save as table" })).toBeNull();
  });
});

describe("save as table — while it runs", () => {
  it("says it is saving and cannot be pressed twice", async () => {
    let finish: (r: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (finish = resolve))),
    );
    render(
      <Shell>
        <MarkingChips markings={[sent]} />
      </Shell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save as table" }));

    const busy = await screen.findByRole("button", { name: "Saving…" });
    expect(busy).toBeDisabled();
    finish(new Response(JSON.stringify({ path: "/markings/x.csv", rows: 1 }), { status: 200 }));
    await waitFor(() => expect(screen.getByText(/Saved 1 row\b/)).toBeInTheDocument());
  });
});
