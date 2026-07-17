// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { AutoGenerateCards } from "./AutoGenerateCards";

const renderModal = (onClose: () => void = () => {}) =>
  render(<AutoGenerateCards collectionId="col-1" client={mockKbApi} onClose={onClose} />, {
    wrapper: QueryWrap,
  });

const checkbox = (name: string) =>
  screen.getByRole("checkbox", { name }) as HTMLInputElement;

describe("AutoGenerateCards picker (#415)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(() => {
    cleanup();
    localStorage.clear(); // FileTree collapse state is keyed by scopeId
    vi.restoreAllMocks();
  });

  it("lists documents under a Documents/ folder as checkboxes", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "reflow.md"));
    renderModal();
    expect(await screen.findByRole("checkbox", { name: "reflow.md" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Documents" })).toBeInTheDocument();
  });

  it("lists wiki pages under a Wiki/ folder when the collection has a wiki", async () => {
    await mockKbApi.writeWikiPage("col-1", "/index.md", "# Index");
    renderModal();
    expect(await screen.findByRole("checkbox", { name: "index.md" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Wiki" })).toBeInTheDocument();
  });

  it("select-all then generate enqueues the picked doc ids and confirms (no spinning)", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "reflow.md"));
    const spy = vi.spyOn(mockKbApi, "generateContextCards");
    const user = userEvent.setup();
    renderModal();
    await screen.findByRole("checkbox", { name: "reflow.md" });

    await user.click(screen.getByRole("button", { name: "全選" }));
    const gen = screen.getByRole("button", { name: /自動生成/ });
    expect(gen).toHaveTextContent("自動生成（1）");

    await user.click(gen);
    expect(spy).toHaveBeenCalledWith("col-1", ["col-1/me/reflow.md"]);
    expect(await screen.findByTestId("cardgen-started")).toBeInTheDocument();
    // no review surface in the modal any more
    expect(screen.queryByTestId("cardgen-proposal")).not.toBeInTheDocument();
  });

  it("submits a selected wiki page by its TYPE-TAGGED id, mixed into the same list", async () => {
    await mockKbApi.writeWikiPage("col-1", "/index.md", "# Index");
    const spy = vi.spyOn(mockKbApi, "generateContextCards");
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByRole("checkbox", { name: "index.md" }));
    await user.click(screen.getByRole("button", { name: /自動生成/ }));
    // `wiki:` tag keeps this distinct from a same-path document's id.
    expect(spy).toHaveBeenCalledWith("col-1", ["wiki:col-1∕index.md"]);
  });

  it("search narrows the tree and select-all picks only the matches", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "reflow.md"));
    await mockKbApi.uploadDocument("col-1", new File(["x"], "other.md"));
    const spy = vi.spyOn(mockKbApi, "generateContextCards");
    const user = userEvent.setup();
    renderModal();
    await screen.findByRole("checkbox", { name: "reflow.md" });

    await user.type(screen.getByLabelText("Search sources"), "reflow");
    expect(screen.queryByRole("checkbox", { name: "other.md" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "全選" }));
    await user.click(screen.getByRole("button", { name: /自動生成/ }));
    expect(spy).toHaveBeenCalledWith("col-1", ["col-1/me/reflow.md"]);
  });

  it("invert flips the visible selection", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "a.md"));
    await mockKbApi.uploadDocument("col-1", new File(["x"], "b.md"));
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByRole("checkbox", { name: "a.md" }));
    await user.click(screen.getByRole("button", { name: "反選" }));
    expect(checkbox("a.md").checked).toBe(false);
    expect(checkbox("b.md").checked).toBe(true);
  });

  it("does not show a still-indexing note when every picked document is ready", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "reflow.md")); // mock docs are ready
    const user = userEvent.setup();
    renderModal();
    await screen.findByRole("checkbox", { name: "reflow.md" });

    await user.click(screen.getByRole("button", { name: "全選" }));
    await user.click(screen.getByRole("button", { name: /自動生成/ }));
    await screen.findByTestId("cardgen-started");
    expect(screen.queryByTestId("cardgen-pending")).not.toBeInTheDocument();
  });

  it("disables generate until at least one source is picked", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "a.md"));
    renderModal();
    await screen.findByRole("checkbox", { name: "a.md" });
    expect(screen.getByRole("button", { name: /自動生成/ })).toBeDisabled();
  });
});
