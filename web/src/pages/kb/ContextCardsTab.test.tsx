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
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks(); // spies are per-test — don't let call counts bleed across tests
  });

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

  it("shows a selected card as a preview by default, editable via Edit", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "The capping layer.",
    });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    // default = preview: the explanation is rendered, not an editable field;
    // Delete is hidden in preview.
    expect(await screen.findByText("The capping layer.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Explanation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    expect(await screen.findByLabelText("Explanation")).toHaveValue("The capping layer.");
    expect(screen.getByRole("button", { name: /delete/i })).toBeInTheDocument();
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

  it("does not create duplicates when Save is pressed again after authoring", async () => {
    const createSpy = vi.spyOn(mockKbApi, "createContextCard");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByRole("button", { name: /new/i }));
    await userEvent.type(screen.getByLabelText("Title"), "Reflow");
    await userEvent.type(screen.getByLabelText("Add a term"), "reflow{enter}");
    await userEvent.type(screen.getByLabelText("Explanation"), "zone 3");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    // wait for the create to land and the card to show in the list
    expect(await screen.findByText("Reflow")).toBeInTheDocument();

    // pressing Save again must NOT author a second card — the draft is now the
    // saved card, so this is an update.
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(await mockKbApi.listContextCards("col-1")).toHaveLength(1);
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
    await userEvent.click(screen.getByRole("tab", { name: "Edit" })); // preview → editor
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

  it("filters the list to an exact name match (Name mode)", async () => {
    await mockKbApi.createContextCard({ collection_id: "col-1", keys: ["M4"], title: "Metal 4", body: "a" });
    await mockKbApi.createContextCard({ collection_id: "col-1", keys: ["SiCN"], title: "Nitride", body: "b" });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    expect(await screen.findByText("Metal 4")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search cards"), "m4");

    expect(screen.getByText("Metal 4")).toBeInTheDocument(); // case-insensitive exact hit
    expect(screen.queryByText("Nitride")).not.toBeInTheDocument(); // filtered out
  });

  it("finds cards mentioned in a pasted passage (In text mode)", async () => {
    await mockKbApi.createContextCard({ collection_id: "col-1", keys: ["M4"], title: "Metal 4", body: "a" });
    await mockKbApi.createContextCard({ collection_id: "col-1", keys: ["SiCN"], title: "Nitride", body: "b" });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);
    await screen.findByText("Metal 4");

    await userEvent.click(screen.getByRole("tab", { name: "In text" }));
    await userEvent.type(screen.getByLabelText("Search cards"), "the M4 reflow step");

    expect(screen.getByText("Metal 4")).toBeInTheDocument();
    expect(screen.queryByText("Nitride")).not.toBeInTheDocument();
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
    await userEvent.click(screen.getByRole("tab", { name: "Edit" })); // Delete lives in Edit only
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));

    expect(spy).toHaveBeenCalledWith(card.id);
  });
});
