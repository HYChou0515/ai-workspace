// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { EditItemModal } from "./WorkspaceShell";

afterEach(cleanup);

function manifest(): AppManifest {
  return {
    slug: "x",
    title: "X",
    icon: "",
    color: "",
    function: { workspace: true, sandbox: true, terminal: false },
    agent: { picker: [] },
    item: { noun: "Item", noun_plural: "Items" },
    layout: { breadcrumb: [], statusbar: [], list: [], default_tabs: [] },
    labels: {},
    fields: [],
    field_styles: {},
  } as unknown as AppManifest;
}

const item = { title: "My Item", description: "" } as unknown as AppItem;

describe("EditItemModal (#445 — ModalShell migration)", () => {
  it("renders the Edit <noun> heading and seeds the form from the item", () => {
    renderWithQuery(
      <EditItemModal manifest={manifest()} item={item} onClose={() => {}} onSubmit={() => {}} />,
    );
    expect(screen.getByRole("heading", { name: "Edit Item" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("My Item")).toBeInTheDocument();
  });

  it("closes when Escape is pressed", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <EditItemModal manifest={manifest()} item={item} onClose={onClose} onSubmit={() => {}} />,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // #779: this modal holds a whole edit form, so neither click may close it —
  // the panel because the click is inside, the backdrop because a stray click
  // beside the panel is exactly how a half-finished edit used to vanish.
  it("ignores clicks on the panel and on the backdrop", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <EditItemModal manifest={manifest()} item={item} onClose={onClose} onSubmit={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("edit-item"));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("edit-item-backdrop"));
    expect(onClose).not.toHaveBeenCalled();
  });

  // #779: this modal is a whole edit form. Escape is a deliberate exit, so it
  // still works — but not silently, and not while there is an unsaved change.
  it("asks before dropping an edited field, and keeps the edit when told to", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <EditItemModal manifest={manifest()} item={item} onClose={onClose} onSubmit={() => {}} />,
    );
    const title = screen.getByLabelText(/title/i);
    await userEvent.clear(title);
    await userEvent.type(title, "Renamed");

    await userEvent.keyboard("{Escape}");

    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByLabelText(/title/i)).toHaveValue("Renamed");
  });
});
