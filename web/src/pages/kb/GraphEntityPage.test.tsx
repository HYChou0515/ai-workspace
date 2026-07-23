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
  claims: [],
};

// #628: one number stated on a slide that names the entity.
const CLAIM = {
  metric: "良率",
  norm_metric: "良率",
  value: "98.7",
  unit: "%",
  period: "Q3",
  norm_period: "q3",
  source_doc_id: "deck-A",
  chunk_id: "deck-A#0",
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

    expect((await screen.findAllByText(/回焊爐/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText("機台").length).toBeGreaterThan(0); // hero + graph centre
    expect(screen.getByText("Reflow Oven")).toBeInTheDocument(); // alias chip
    // evidence doc appears as a GRAPH NODE and in the receipts list below
    expect(screen.getAllByText("deck-A").length).toBeGreaterThan(1);
    expect(screen.getByText("同名")).toBeInTheDocument(); // basis translated, not "identical"
    // the graph renders, and the relation links to the OTHER identity's page
    expect(screen.getByTestId("entity-graph")).toBeInTheDocument();
    const rels = screen.getAllByRole("link", { name: "產線三" });
    expect(rels.length).toBeGreaterThan(0);
    for (const rel of rels) {
      expect(rel).toHaveAttribute("href", "/kb/graph/entities/graph-entity:2");
    }
  });

  it("renders not-found for a 404 — unknown and unreadable look the same", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    renderAt("graph-entity:nope");
    expect(await screen.findByTestId("entity-missing")).toBeInTheDocument();
  });

  it("shows the numbers stated beside it, each with its slide (#628)", async () => {
    const withClaim = { ...ENTITY, claims: [CLAIM] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(withClaim), { status: 200 })),
    );
    renderAt("graph-entity:1");

    const section = await screen.findByTestId("entity-claims");
    expect(section).toHaveTextContent("良率");
    expect(section).toHaveTextContent("98.7");
    expect(section).toHaveTextContent("%");
    expect(section).toHaveTextContent("Q3");
    expect(section).toHaveTextContent("deck-A"); // the slide it came from
    // one figure, one voice — nothing disagrees
    expect(screen.queryByText("數字對不上")).not.toBeInTheDocument();
  });

  it("flags figures that disagree for the same metric and period (#628)", async () => {
    const disagreeing = {
      ...ENTITY,
      claims: [
        CLAIM,
        { ...CLAIM, value: "95.0", source_doc_id: "deck-B", chunk_id: "deck-B#0" },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(disagreeing), { status: 200 })),
    );
    renderAt("graph-entity:1");

    const section = await screen.findByTestId("entity-claims");
    expect(section).toHaveTextContent("98.7");
    expect(section).toHaveTextContent("95.0");
    // both rows of the disagreeing pair wear the badge
    expect(screen.getAllByText("數字對不上")).toHaveLength(2);
  });

  it("hides the numbers section when there are none (#628)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(ENTITY), { status: 200 })),
    );
    renderAt("graph-entity:1");
    await screen.findByTestId("entity-page");
    expect(screen.queryByTestId("entity-claims")).not.toBeInTheDocument();
  });
});
