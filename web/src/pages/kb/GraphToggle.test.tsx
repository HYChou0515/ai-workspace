// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbCollection } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { GraphToggle } from "./GraphToggle";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  is_global: false,
  auto_digest: false,
  use_graph: false,
  ...over,
});

function client(over: Partial<KbApi> = {}): KbApi {
  return { ...mockKbApi, ...over };
}

describe("GraphToggle", () => {
  it("reflects the collection's use_graph state", () => {
    render(<GraphToggle collection={coll({ use_graph: true })} client={client()} />);
    expect(screen.getByTestId("kb-usegraph-toggle")).toBeChecked();
  });

  it("is unchecked when use_graph is off", () => {
    render(<GraphToggle collection={coll({ use_graph: false })} client={client()} />);
    expect(screen.getByTestId("kb-usegraph-toggle")).not.toBeChecked();
  });

  it("persists the choice via updateCollection on change", async () => {
    const updateCollection = vi.fn(async () => {});
    render(
      <GraphToggle
        collection={coll({ resource_id: "c9", use_graph: false })}
        client={client({ updateCollection })}
      />,
    );
    fireEvent.click(screen.getByTestId("kb-usegraph-toggle"));
    await waitFor(() => expect(updateCollection).toHaveBeenCalledWith("c9", { use_graph: true }));
  });

  // Flipping the switch must not start a corpus-wide VLM pass on its own — that
  // is what the explicit button is for.
  it("does not start an extraction just because the toggle was flipped", async () => {
    const updateCollection = vi.fn(async () => {});
    const rebuildGraph = vi.fn(async () => ({ queued: 0, status: "rebuilding" }));
    render(
      <GraphToggle
        collection={coll({ use_graph: false })}
        client={client({ updateCollection, rebuildGraph })}
      />,
    );
    fireEvent.click(screen.getByTestId("kb-usegraph-toggle"));
    await waitFor(() => expect(updateCollection).toHaveBeenCalled());
    expect(rebuildGraph).not.toHaveBeenCalled();
  });

  describe("extract now", () => {
    it("queues an extraction for this collection", async () => {
      const rebuildGraph = vi.fn(async () => ({ queued: 3, status: "rebuilding" }));
      render(
        <GraphToggle
          collection={coll({ resource_id: "c9", use_graph: true })}
          client={client({ rebuildGraph })}
        />,
      );
      fireEvent.click(screen.getByTestId("kb-usegraph-rebuild"));
      await waitFor(() => expect(rebuildGraph).toHaveBeenCalledWith("c9"));
    });

    // The route answers `disabled` for an opted-out collection, so offering the
    // button there would promise work that never happens.
    it("is disabled while the collection has not opted in", () => {
      render(<GraphToggle collection={coll({ use_graph: false })} client={client()} />);
      expect(screen.getByTestId("kb-usegraph-rebuild")).toBeDisabled();
    });

    it("reports how many documents the run covers", async () => {
      const rebuildGraph = vi.fn(async () => ({ queued: 7, status: "rebuilding" }));
      render(<GraphToggle collection={coll({ use_graph: true })} client={client({ rebuildGraph })} />);
      fireEvent.click(screen.getByTestId("kb-usegraph-rebuild"));
      await waitFor(() => expect(screen.getByTestId("kb-usegraph-result")).toHaveTextContent("7"));
    });

    it("stays quiet until it is pressed", () => {
      render(<GraphToggle collection={coll({ use_graph: true })} client={client()} />);
      expect(screen.queryByTestId("kb-usegraph-result")).not.toBeInTheDocument();
    });
  });
});

describe("the corpus's own extraction criterion (#697)", () => {
  // The prompt's own description of what to pull out — "anything a reader would
  // consider a distinct thing" — admits every noun, so a capable model fills the
  // graph with generic words and every label a table carried. What deserves to
  // exist is domain knowledge, and it belongs to whoever owns the collection —
  // which means they have to be able to type it somewhere.

  it("shows the collection's criterion so its owner can read what is in force", () => {
    render(<GraphToggle collection={coll({ use_graph: true, graph_guidance: "只收機台與缺陷。" })} client={client()} />);
    expect(screen.getByTestId("kb-graph-guidance")).toHaveValue("只收機台與缺陷。");
  });

  it("saves an edited criterion onto the collection", async () => {
    const updateCollection = vi.fn(async () => ({}));
    render(
      <GraphToggle
        collection={coll({ use_graph: true, graph_guidance: "" })}
        client={client({ updateCollection } as unknown as Partial<KbApi>)}
      />,
    );
    fireEvent.change(screen.getByTestId("kb-graph-guidance"), {
      target: { value: "只收機台、製程、缺陷、參數與料號。" },
    });
    fireEvent.click(screen.getByTestId("kb-graph-guidance-save"));
    await waitFor(() =>
      expect(updateCollection).toHaveBeenCalledWith("c1", {
        graph_guidance: "只收機台、製程、缺陷、參數與料號。",
      }),
    );
  });

  it("cannot be saved until it differs, so an accidental press writes no revision", () => {
    render(<GraphToggle collection={coll({ use_graph: true, graph_guidance: "abc" })} client={client()} />);
    expect(screen.getByTestId("kb-graph-guidance-save")).toBeDisabled();
  });

  it("says so when the save fails, instead of looking exactly like a save", async () => {
    // #697 P19: with no error state, a refused save leaves the typed criterion
    // in the box and the button live again — which is what a SUCCESSFUL save
    // followed by one more keystroke also looks like. The owner walks away
    // believing the corpus is being extracted under the new criterion, and it
    // is not. Reachable for anyone without write_meta who can open the panel,
    // and for any transient 5xx.
    const updateCollection = vi.fn(async () => {
      throw Object.assign(new Error("403"), { status: 403 });
    });
    render(
      <GraphToggle
        collection={coll({ use_graph: true, graph_guidance: "" })}
        client={client({ updateCollection } as unknown as Partial<KbApi>)}
      />,
    );
    fireEvent.change(screen.getByTestId("kb-graph-guidance"), { target: { value: "只收機台。" } });
    fireEvent.click(screen.getByTestId("kb-graph-guidance-save"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("confirms a save that landed, so the two outcomes do not look alike", async () => {
    const updateCollection = vi.fn(async () => {});
    render(
      <GraphToggle
        collection={coll({ use_graph: true, graph_guidance: "" })}
        client={client({ updateCollection } as unknown as Partial<KbApi>)}
      />,
    );
    fireEvent.change(screen.getByTestId("kb-graph-guidance"), { target: { value: "只收機台。" } });
    fireEvent.click(screen.getByTestId("kb-graph-guidance-save"));

    await waitFor(() =>
      expect(screen.getByTestId("kb-graph-guidance-save")).toHaveTextContent("已儲存"),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
