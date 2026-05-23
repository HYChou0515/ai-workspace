// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchOptions, SearchResult } from "../../api/types";
import { SearchPanel } from "./SearchPanel";

afterEach(cleanup);

function stubClient(results: SearchResult[]) {
  return {
    searchFiles: vi.fn(
      async (_id: string, _q: string, _o?: SearchOptions): Promise<SearchResult[]> => results,
    ),
    replaceInFiles: vi.fn(async (): Promise<number> => 0),
  };
}

const SAMPLE: SearchResult[] = [
  {
    path: "/a.md",
    matches: [
      { line: 1, col: 1, text: "void rate spiked" },
      { line: 3, col: 1, text: "VOID again" },
    ],
  },
  { path: "/data/x.csv", matches: [{ line: 1, col: 1, text: "void in csv" }] },
];

describe("<SearchPanel />", () => {
  it("searches as you type and groups matches by file", async () => {
    const user = userEvent.setup();
    const client = stubClient(SAMPLE);
    render(<SearchPanel investigationId="inv1" onOpenFile={vi.fn()} client={client} />);

    await user.type(screen.getByPlaceholderText(/search/i), "void");
    await waitFor(() => expect(client.searchFiles).toHaveBeenCalled());

    expect(await screen.findByText("a.md")).toBeInTheDocument();
    expect(screen.getByText("x.csv")).toBeInTheDocument();
    // a summary count of total matches across files
    expect(screen.getByText(/3 results? in 2 files?/i)).toBeInTheDocument();
  });

  it("opens the file when a match line is clicked", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    render(
      <SearchPanel investigationId="inv1" onOpenFile={onOpenFile} client={stubClient(SAMPLE)} />,
    );
    await user.type(screen.getByPlaceholderText(/search/i), "void");
    const row = await screen.findByTitle("/a.md:1:1");
    await user.click(row);
    expect(onOpenFile).toHaveBeenCalledWith("/a.md", expect.anything());
  });

  it("passes regex / case / word toggles through to the client", async () => {
    const user = userEvent.setup();
    const client = stubClient([]);
    render(<SearchPanel investigationId="inv1" onOpenFile={vi.fn()} client={client} />);

    await user.click(screen.getByRole("button", { name: /match case/i }));
    await user.click(screen.getByRole("button", { name: /whole word/i }));
    await user.click(screen.getByRole("button", { name: /regex/i }));
    await user.type(screen.getByPlaceholderText(/search/i), "err");

    await waitFor(() => {
      const lastCall = client.searchFiles.mock.calls.at(-1);
      expect(lastCall?.[2]).toMatchObject({
        caseSensitive: true,
        wholeWord: true,
        regex: true,
      });
    });
  });

  it("replace all runs replaceInFiles then re-searches", async () => {
    const user = userEvent.setup();
    const client = stubClient(SAMPLE);
    render(<SearchPanel investigationId="inv1" onOpenFile={vi.fn()} client={client} />);

    await user.type(screen.getByPlaceholderText(/search/i), "void");
    await screen.findByText("a.md");

    await user.click(screen.getByRole("button", { name: /show replace/i }));
    await user.type(screen.getByPlaceholderText(/replace/i), "VOID");
    await user.click(screen.getByRole("button", { name: /replace all/i }));

    await waitFor(() =>
      expect(client.replaceInFiles).toHaveBeenCalledWith(
        "inv1",
        "void",
        "VOID",
        expect.anything(),
      ),
    );
  });

  it("shows include / exclude inputs and forwards them", async () => {
    const user = userEvent.setup();
    const client = stubClient([]);
    render(<SearchPanel investigationId="inv1" onOpenFile={vi.fn()} client={client} />);
    await user.type(screen.getByPlaceholderText(/files to include/i), "*.md");
    await user.type(screen.getByPlaceholderText(/files to exclude/i), "data/**");
    await user.type(screen.getByPlaceholderText(/search/i), "x");
    await waitFor(() => {
      const lastCall = client.searchFiles.mock.calls.at(-1);
      expect(lastCall?.[2]).toMatchObject({ include: "*.md", exclude: "data/**" });
    });
  });
});
