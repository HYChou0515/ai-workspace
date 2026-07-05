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

  it("closes on a backdrop click but not on a panel click", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <EditItemModal manifest={manifest()} item={item} onClose={onClose} onSubmit={() => {}} />,
    );
    // A click that lands on the panel must NOT bubble out to a close.
    await userEvent.click(screen.getByTestId("edit-item"));
    expect(onClose).not.toHaveBeenCalled();
    // A click on the dimmed backdrop dismisses the modal.
    await userEvent.click(screen.getByTestId("edit-item-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
