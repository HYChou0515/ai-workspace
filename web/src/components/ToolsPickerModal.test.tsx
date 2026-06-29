// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
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

function fakeClient(tools = TOOLS) {
  return { getItemTools: vi.fn(async () => tools) };
}

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
