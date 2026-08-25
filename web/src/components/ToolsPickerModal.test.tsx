// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExternalToolState, ItemToolState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { ToolsPickerModal } from "./ToolsPickerModal";

afterEach(cleanup);

const TOOLS: ItemToolState[] = [
  { key: "exec", label: "Exec", description: "Run a shell command.", default_on: true, pref: "follow", effective: true },
  {
    key: "rca-tools",
    label: "RCA Tools",
    description: "Bundled tools.",
    default_on: true,
    pref: "off",
    effective: false,
  },
];

function fakeClient(tools = TOOLS, external: ExternalToolState[] = []) {
  return { getItemTools: vi.fn(async () => ({ tools, external })) };
}

describe("ToolsPickerModal third-party section (#724)", () => {
  const render = (external: ExternalToolState[]) =>
    renderWithQuery(
      <ToolsPickerModal
        slug="rca"
        itemId="i1"
        onSave={vi.fn()}
        onClose={vi.fn()}
        client={fakeClient(TOOLS, external)}
      />,
    );

  it("names which release is running and who published it", async () => {
    render([
      {
        key: "wafer-history",
        version: "1.4.2",
        author: "Wafer Team <wafer@example.com>",
        stale: false,
        unavailable: null,
      },
    ]);

    const row = await screen.findByTestId("external-tool-wafer-history");
    expect(row).toHaveTextContent("wafer-history");
    expect(row).toHaveTextContent("1.4.2");
    expect(row).toHaveTextContent("Wafer Team <wafer@example.com>");
  });

  it("says so when a tool is running from the cached copy", async () => {
    render([
      { key: "wafer-history", version: "1.4.2", author: null, stale: true, unavailable: null },
    ]);

    // Usable, so it is not an error — but a version number that might be a
    // release behind is worse than none at all if it does not say so.
    expect(await screen.findByTestId("external-tool-wafer-history-stale")).toBeInTheDocument();
  });

  it("lists a declared tool that could not be resolved, with the reason", async () => {
    render([
      {
        key: "legacy-fetch",
        version: null,
        author: null,
        stale: false,
        unavailable: "404 — the artifact expired",
      },
    ]);

    const row = await screen.findByTestId("external-tool-legacy-fetch");
    expect(row).toHaveTextContent("404 — the artifact expired");
  });

  it("shows no third-party heading when the app declares none", async () => {
    render([]);

    await screen.findByTestId("tool-exec-follow"); // the modal has loaded
    expect(screen.queryByTestId("external-tools")).not.toBeInTheDocument();
  });
});

describe("ToolsPickerModal", () => {
  it("seeds the tri-state from the server-resolved per-tool state", async () => {
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={vi.fn()} onClose={vi.fn()} client={fakeClient()} />,
    );
    expect(await screen.findByTestId("tool-rca-tools-off")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("tool-exec-follow")).toHaveAttribute("aria-pressed", "true");
  });

  it("Save stays disabled until the override changes", async () => {
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={vi.fn()} onClose={vi.fn()} client={fakeClient()} />,
    );
    expect(await screen.findByTestId("tools-save")).toBeDisabled();
  });

  it("persists only the sparse override and closes on Save", async () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={onSave} onClose={onClose} client={fakeClient()} />,
    );
    fireEvent.click(await screen.findByTestId("tool-exec-off")); // pin exec off
    fireEvent.click(screen.getByTestId("tools-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ "rca-tools": false, exec: false }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("a clean cancel closes immediately (no discard prompt)", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={vi.fn()} onClose={onClose} client={fakeClient()} />,
    );
    fireEvent.click(await screen.findByTestId("tools-cancel"));
    expect(onClose).toHaveBeenCalled();
  });
});
