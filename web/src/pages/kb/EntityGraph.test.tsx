// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { EntityGraph } from "./EntityGraph";

afterEach(cleanup);

const DOCS = [{ source_doc_id: "deck-A.pptx", surface: "良率", occurrences: 3 }];
const RELS = [
  { direction: "in", predicate: "影響", other_name: "回焊爐", other_entity_id: "e:oven" },
  { direction: "in", predicate: "影響", other_name: "空洞率", other_entity_id: "e:void" },
  { direction: "in", predicate: "影響", other_name: "稼動率", other_entity_id: "e:util" },
];

const show = (links: Parameters<typeof EntityGraph>[0]["links"] = []) =>
  render(
    <MemoryRouter>
      <EntityGraph name="良率" kind="指標" docs={DOCS} rels={RELS} links={links} />
    </MemoryRouter>,
  );

describe("EntityGraph — the edges between neighbours", () => {
  // A star says what the centre touches. It cannot say that 回焊爐 reaches 良率
  // BOTH directly and through 空洞率, which is the shape a reader is after.
  it("draws an edge between two neighbours that are connected", () => {
    show([{ from_entity_id: "e:oven", to_entity_id: "e:void", predicate: "影響" }]);
    expect(screen.getByTestId("entity-graph-neighbor-edges").children).toHaveLength(1);
  });

  it("draws nothing extra when the neighbours are unconnected", () => {
    show([]);
    expect(screen.queryByTestId("entity-graph-neighbor-edges")).not.toBeInTheDocument();
  });

  it("ignores an edge whose far end is not on this page", () => {
    // The centre reaches it, the payload names it, but it was not among the
    // neighbours drawn — there is no node to attach the line to.
    show([{ from_entity_id: "e:oven", to_entity_id: "e:elsewhere", predicate: "影響" }]);
    expect(screen.queryByTestId("entity-graph-neighbor-edges")).not.toBeInTheDocument();
  });

  it("still draws the centre's own spokes", () => {
    show([{ from_entity_id: "e:oven", to_entity_id: "e:void", predicate: "影響" }]);
    expect(screen.getByTestId("entity-graph")).toBeInTheDocument();
    expect(screen.getByText("回焊爐")).toBeInTheDocument();
  });
});
