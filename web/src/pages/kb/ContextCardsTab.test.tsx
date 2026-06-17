// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { ContextCardsTab } from "./ContextCardsTab";

// The body editor is Monaco (lazy + heavy); stub it to a plain textarea so the
// test can read/type the explanation.
vi.mock("../../components/MonacoEditor", () => ({
  MonacoEditor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea
      aria-label="Explanation"
      value={value}
      onChange={(e) => onChange?.((e.target as HTMLTextAreaElement).value)}
    />
  ),
}));

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

describe("ContextCardsTab (#106)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("lists a collection's cards by their label", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4", "capping"],
      title: "Metal-4 capping",
      body: "b",
    });
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["SiCN"],
      title: "",
      body: "b2",
    });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    expect(await screen.findByText("Metal-4 capping")).toBeInTheDocument();
    expect(screen.getByText("SiCN")).toBeInTheDocument(); // titleless → first key
  });

  it("opens a card's explanation in the editor when selected", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "The capping layer.",
    });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    expect(await screen.findByLabelText("Explanation")).toHaveValue("The capping layer.");
  });

  it("authors a new card and saves it through createContextCard", async () => {
    const spy = vi.spyOn(mockKbApi, "createContextCard");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByRole("button", { name: /new/i }));
    await userEvent.type(screen.getByLabelText("Title"), "Reflow zone");
    const term = screen.getByLabelText("Add a term");
    await userEvent.type(term, "reflow{enter}");
    await userEvent.type(screen.getByLabelText("Explanation"), "Zone 3 at 245C.");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(spy).toHaveBeenCalledWith({
      collection_id: "col-1",
      keys: ["reflow"],
      title: "Reflow zone",
      body: "Zone 3 at 245C.",
    });
  });

  it("edits an existing card through updateContextCard", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "old",
    });
    const [card] = await mockKbApi.listContextCards("col-1");
    const spy = vi.spyOn(mockKbApi, "updateContextCard");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    const body = await screen.findByLabelText("Explanation");
    await userEvent.clear(body);
    await userEvent.type(body, "new body");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(spy).toHaveBeenCalledWith(card.id, {
      keys: ["M4"],
      title: "Metal 4",
      body: "new body",
    });
  });

  it("deletes the selected card through deleteContextCard", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
    });
    const [card] = await mockKbApi.listContextCards("col-1");
    const spy = vi.spyOn(mockKbApi, "deleteContextCard");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));

    expect(spy).toHaveBeenCalledWith(card.id);
  });
});
