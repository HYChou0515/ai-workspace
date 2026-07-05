// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppManifest } from "../api/types";
import { ItemForm } from "./ItemForm";

afterEach(cleanup);

// ItemForm resolves the (optional) owner via useUser; stub it so the unit test
// stays hermetic and needs no QueryClient.
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [],
  useUser: (id: string) => ({ id, name: id, section: "", email: "", photo_url: null }),
}));

const manifest = {
  item: { noun: "investigation", noun_plural: "investigations", create_label: "New" },
  layout: { breadcrumb: [], statusbar: [], list: [], form: ["severity", "topics", "product"], default_tabs: [] },
  fields: [
    { name: "title", label: "Title", kind: "text" },
    { name: "description", label: "Description", kind: "text" },
    { name: "severity", label: "Severity", kind: "select", options: ["P0", "P2"] },
    { name: "topics", label: "Topics", kind: "tags" },
    { name: "product", label: "Product", kind: "text" },
  ],
  labels: { severity: "Severity" },
} as unknown as AppManifest;

describe("ItemForm", () => {
  it("renders a segmented picker for an enum field and text inputs for text fields", () => {
    render(<ItemForm manifest={manifest} submitLabel="Create" onSubmit={vi.fn()} />);
    // enum form field → a group of option buttons (not a <select>)
    const sev = screen.getByRole("group", { name: "Severity" });
    expect(within(sev).getByRole("button", { name: "P0" })).toBeInTheDocument();
    expect(within(sev).getByRole("button", { name: "P2" })).toBeInTheDocument();
    // Tier-1 title is always present as a text input
    expect((screen.getByLabelText("Title") as HTMLElement).tagName).toBe("INPUT");
  });

  it("blocks submit with an empty title and shows a required message (no silent no-op)", () => {
    const onSubmit = vi.fn();
    render(<ItemForm manifest={manifest} submitLabel="Create" onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/title is required/i)).toBeInTheDocument();
  });

  it("marks the tag-remove control as destructive so it reads as more than a close x (#466)", () => {
    render(<ItemForm manifest={manifest} submitLabel="Create" onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Topics"), { target: { value: "Reflow" } });
    fireEvent.keyDown(screen.getByLabelText("Topics"), { key: "Enter" });
    // The `.tag-remove` class supplies the danger-on-hover cue (a bare muted x
    // gave no signal that it removes the tag).
    expect(screen.getByRole("button", { name: "Remove Reflow" }).className).toContain("tag-remove");
  });

  it("collects field values (incl. picker + tags) and submits them", () => {
    const onSubmit = vi.fn();
    render(<ItemForm manifest={manifest} submitLabel="Create" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Oven drift" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Severity" })).getByRole("button", { name: "P0" }));
    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "MX-7" } });
    // tags: type a topic + Enter adds a chip
    fireEvent.change(screen.getByLabelText("Topics"), { target: { value: "Reflow" } });
    fireEvent.keyDown(screen.getByLabelText("Topics"), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Oven drift",
        severity: "P0",
        product: "MX-7",
        topics: ["Reflow"],
      }),
    );
  });
});
