// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { KbContextCard } from "../../api/kb";
import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import { LocaleProvider } from "../../lib/i18n";
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

// ContextCardsTab is URL-driven (#93): the open card is the :cardId param.
// Mount it under both the bare-tab and the open-card routes so selecting a card
// navigates and the same route re-renders with it open.
const render = (ui: ReactElement, start = "/kb/collections/col-1/cards") =>
  rtlRender(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route path="/kb/collections/:cid/cards" element={ui} />
        <Route path="/kb/collections/:cid/cards/:cardId" element={ui} />
      </Routes>
    </MemoryRouter>,
    { wrapper: QueryWrap },
  );

describe("ContextCardsTab (#106)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks(); // spies are per-test — don't let call counts bleed across tests
  });

  it("localizes the auto-generate button — English under an English locale (#456)", async () => {
    localStorage.setItem("ws.locale", "en");
    rtlRender(
      <LocaleProvider>
        <MemoryRouter initialEntries={["/kb/collections/col-1/cards"]}>
          <Routes>
            <Route
              path="/kb/collections/:cid/cards"
              element={<ContextCardsTab collectionId="col-1" client={mockKbApi} />}
            />
          </Routes>
        </MemoryRouter>
      </LocaleProvider>,
      { wrapper: QueryWrap },
    );
    expect(await screen.findByRole("button", { name: /Auto-generate/i })).toBeInTheDocument();
    expect(screen.queryByText("⚡ 自動生成")).not.toBeInTheDocument();
  });

  it("shows a loading placeholder while cards are still fetching — not the empty copy", () => {
    const client = {
      ...mockKbApi,
      listContextCards: () => new Promise<KbContextCard[]>(() => {}),
    } as typeof mockKbApi;
    render(<ContextCardsTab collectionId="col-1" client={client} />);
    expect(screen.getByTestId("kb-cards-loading")).toBeInTheDocument();
    expect(screen.queryByText(/No cards found/)).not.toBeInTheDocument();
  });

  it("shows the empty copy only once loading resolves with no cards", async () => {
    render(<ContextCardsTab collectionId="empty-col" client={mockKbApi} />);
    expect(await screen.findByText(/No cards found/)).toBeInTheDocument();
    expect(screen.queryByTestId("kb-cards-loading")).not.toBeInTheDocument();
  });

  it("pitches the glossary's purpose with an example when there are no cards (#173)", async () => {
    render(<ContextCardsTab collectionId="empty-col" client={mockKbApi} />);
    // The editor pane explains WHY to build one, with a concrete example —
    // not just "select a card".
    expect(await screen.findByText(/還沒有詞彙卡/)).toBeInTheDocument();
    expect(screen.getByText(/COGS/)).toBeInTheDocument();
  });

  it("shows a neutral hint when cards exist but none is selected (#173)", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
    });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    // A card is listed, so the pane just invites picking one — no purpose pitch.
    expect(await screen.findByText("Metal 4")).toBeInTheDocument();
    expect(screen.getByText(/選一張詞彙卡，或新增一張/)).toBeInTheDocument();
    expect(screen.queryByText(/還沒有詞彙卡/)).not.toBeInTheDocument();
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

  it("deep-links straight to a card's preview (#93)", async () => {
    const id = await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "The capping layer.",
    });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />, `/kb/collections/col-1/cards/${id}`);
    // opens from the URL as a preview, no click
    expect(await screen.findByText("The capping layer.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Explanation")).not.toBeInTheDocument();
  });

  it("labels the create button consistently: a plus icon + 'New card', no bare + prefix (#466)", async () => {
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);
    const btn = await screen.findByRole("button", { name: /new card/i });
    // Matches the KbCollectionsGrid "New collection" pattern: icon carries the "+",
    // the label is the object-scoped noun — not a textual "+ New card".
    expect(btn.textContent?.trim()).toBe("New card"); // no textual "+"
    expect(btn.querySelector('[data-icon="plus"]')).toBeInTheDocument();
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
      // #518: the save now carries the card's linked docs (empty for a fresh card)
      // so a person editing a card can no longer silently wipe them.
      reference_doc_ids: [],
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
      reference_doc_ids: [],
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

  it("preserves a card's linked documents when only its body is edited (#518)", async () => {
    // The whole reason the field is threaded through: before this, the editor
    // sent {keys,title,body} and an edit silently wiped the links.
    const ref = encodeURIComponent("col-1/u/spec.pdf");
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "old",
      reference_doc_ids: [ref],
    });
    const spy = vi.spyOn(mockKbApi, "updateContextCard");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    // the linked doc is visible in the editor
    expect(screen.getByText("spec.pdf")).toBeInTheDocument();
    const body = await screen.findByLabelText("Explanation");
    await userEvent.clear(body);
    await userEvent.type(body, "new body");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(spy).toHaveBeenCalledWith(expect.any(String), {
      keys: ["M4"],
      title: "Metal 4",
      body: "new body",
      reference_doc_ids: [ref], // survived the edit
    });
  });

  it("detaching a linked document drops it on save (#518, detach = unlink)", async () => {
    const keep = encodeURIComponent("col-1/u/keep.pdf");
    const drop = encodeURIComponent("col-1/u/drop.png");
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
      reference_doc_ids: [keep, drop],
    });
    const spy = vi.spyOn(mockKbApi, "updateContextCard");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: /Detach drop.png/ }));
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(spy).toHaveBeenCalledWith(expect.any(String), {
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
      reference_doc_ids: [keep], // drop.png unlinked, keep.pdf stays
    });
  });

  it("drop-to-create uploads a file and links the new document (#518)", async () => {
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
    });
    const uploadSpy = vi.spyOn(mockKbApi, "uploadDocument");
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));

    const input = screen.getByTestId("card-attach-input") as HTMLInputElement;
    const file = new File(["pixels"], "diagram.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    // the resulting doc shows up as a linked chip (await the async upload)...
    expect(await screen.findByText("diagram.png")).toBeInTheDocument();
    // ...having gone through the normal ingest pipeline
    expect(uploadSpy).toHaveBeenCalledWith("col-1", file);
  });

  it("opens a linked document in the viewer when its chip is clicked (#518)", async () => {
    // The link is worthless if a human can't open what it points at.
    const ref = encodeURIComponent("col-1/u/spec.pdf");
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
      reference_doc_ids: [ref],
    });
    const onOpenDoc = vi.fn();
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} onOpenDoc={onOpenDoc} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: /Open spec.pdf/ }));

    expect(onOpenDoc).toHaveBeenCalledWith(ref);
  });

  it("renders an image attachment as a thumbnail, not a pill (#518)", async () => {
    const png = encodeURIComponent("col-1/u/diagram.png");
    await mockKbApi.createContextCard({
      collection_id: "col-1",
      keys: ["M4"],
      title: "Metal 4",
      body: "b",
      reference_doc_ids: [png],
    });
    render(<ContextCardsTab collectionId="col-1" client={mockKbApi} />);

    await userEvent.click(await screen.findByText("Metal 4"));
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));

    // the image shows itself (thumbnail), not a "diagram.png" text pill
    expect(await screen.findByRole("img", { name: /diagram.png/ })).toBeInTheDocument();
    expect(screen.queryByText("diagram.png")).not.toBeInTheDocument();
  });
});
