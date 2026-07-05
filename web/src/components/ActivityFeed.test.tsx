// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityCatalog } from "../api/entities";
import type { ActivityEntry } from "../api/types";
import { ActivityFeed } from "./ActivityFeed";

vi.mock("../hooks/useResources", () => ({ useActivity: vi.fn() }));
vi.mock("../hooks/useEntities", () => ({ useEntityCatalog: vi.fn() }));
import { useActivity } from "../hooks/useResources";
import { useEntityCatalog } from "../hooks/useEntities";

afterEach(cleanup);

const catalog: EntityCatalog = {
  types: [{ name: "issue", records_path: "issues", fields: [], form: [] }],
  diagnostics: [],
};

function setup(entries: ActivityEntry[], cat: EntityCatalog | undefined = catalog) {
  vi.mocked(useActivity).mockReturnValue(entries);
  vi.mocked(useEntityCatalog).mockReturnValue({ data: cat } as ReturnType<typeof useEntityCatalog>);
  const onOpenFile = vi.fn();
  render(<ActivityFeed slug="pm" itemId="A" onOpenFile={onOpenFile} />);
  return { onOpenFile };
}

const e = (ref: ActivityEntry["ref"], text: string, kind = "entity_created"): ActivityEntry => ({
  ts: "2026-07-05T00:00:00Z",
  kind,
  text,
  ref,
});

describe("ActivityFeed (#455 P3)", () => {
  it("lists only this item's activity", () => {
    setup([
      e({ investigation_id: "A", type: "issue", number: 1 }, "Created issue #1"),
      e({ investigation_id: "B", type: "issue", number: 2 }, "Created issue #2"),
    ]);
    expect(screen.getByText("Created issue #1")).toBeInTheDocument();
    expect(screen.queryByText("Created issue #2")).not.toBeInTheDocument();
  });

  it("opens an entity's record file when its row is clicked", () => {
    const { onOpenFile } = setup([e({ investigation_id: "A", type: "issue", number: 7 }, "Updated issue #7")]);
    fireEvent.click(screen.getByRole("button", { name: /Updated issue #7/ }));
    expect(onOpenFile).toHaveBeenCalledWith("/issues/7.md", { preview: true });
  });

  it("renders a non-openable event as plain text, not a button", () => {
    setup([e({ investigation_id: "A" }, "Investigation created", "investigation_created")]);
    expect(screen.getByText("Investigation created")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Investigation created/ })).not.toBeInTheDocument();
  });

  it("shows an empty state when the item has no activity", () => {
    setup([e({ investigation_id: "B" }, "elsewhere")]);
    expect(screen.getByText(/no activity/i)).toBeInTheDocument();
  });
});
