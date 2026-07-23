// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../../test/queryWrapper";
import { GraphEntityPage } from "./GraphEntityPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ENTITY = {
  id: "graph-entity:1",
  name: "回焊爐",
  aliases: ["Reflow Oven"],
  kind: "機台",
  occurrences: 4,
  mentions: [
    {
      surface: "回焊爐",
      source_doc_id: "deck-A",
      occurrences: 2,
      chunk_ids: [],
      basis: "identical",
      evidence: "",
    },
  ],
  related: [
    {
      direction: "out",
      predicate: "位於",
      other_name: "產線三",
      other_entity_id: "graph-entity:2",
      quote: "回焊爐位於產線三",
      source_doc_id: "deck-A",
      chunk_id: "deck-A#0",
    },
  ],
};

const renderAt = (id: string) =>
  render(
    <MemoryRouter initialEntries={[`/kb/graph/entities/${id}`]}>
      <Routes>
        <Route path="/kb/graph/entities/:entityId" element={<GraphEntityPage />} />
      </Routes>
    </MemoryRouter>,
    { wrapper: QueryWrap },
  );

describe("GraphEntityPage (#534)", () => {
  it("shows the identity: name, kind, aliases, evidence docs and relations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(ENTITY), { status: 200 })),
    );
    renderAt("graph-entity:1");

    expect(await screen.findByText("回焊爐")).toBeInTheDocument();
    expect(screen.getByText("機台")).toBeInTheDocument();
    expect(screen.getByText(/Reflow Oven/)).toBeInTheDocument(); // alias line
    expect(screen.getByText("deck-A")).toBeInTheDocument(); // evidence doc link
    // the relation links to the OTHER identity's page
    const rel = screen.getByRole("link", { name: "產線三" });
    expect(rel).toHaveAttribute("href", "/kb/graph/entities/graph-entity:2");
  });

  it("renders not-found for a 404 — unknown and unreadable look the same", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    renderAt("graph-entity:nope");
    expect(await screen.findByTestId("entity-missing")).toBeInTheDocument();
  });
});
