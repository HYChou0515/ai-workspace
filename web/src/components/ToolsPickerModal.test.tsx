// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { ToolsPickerModal } from "./ToolsPickerModal";

afterEach(cleanup);

const TOOLS: ItemToolState[] = [
  { key: "exec", group: "builtin", label: "Exec", description: "Run a shell command.", default_on: true, pref: "follow", effective: true },
  {
    key: "rca-tools",
    group: "rca-tools",
    label: "RCA Tools",
    description: "Bundled tools.",
    default_on: true,
    pref: "off",
    effective: false,
  },
];

function fakeClient(tools = TOOLS, over: { updateNeedsClose?: boolean; canClose?: boolean } = {}) {
  return {
    getItemTools: vi.fn(async () => ({
      tools,
      updateNeedsClose: over.updateNeedsClose ?? false,
      canClose: over.canClose ?? false,
    })),
  };
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

  // Every real app grants 11–23 built-ins, so the modal's real shape is a
  // folded core group — the two-row fixture above never draws a fold at all.
  it("a fold's tri-state reaches Save as one pinned key per row", async () => {
    const onSave = vi.fn();
    const many: ItemToolState[] = [
      ...TOOLS,
      { ...TOOLS[0]!, key: "read_file", label: "Read File" },
      { ...TOOLS[0]!, key: "write_file", label: "Write File" },
    ];
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={onSave} onClose={vi.fn()} client={fakeClient(many)} />,
    );
    const header = await screen.findByTestId("tool-group-header-builtin");
    expect(header).toHaveAttribute("aria-expanded", "false"); // nothing mixed on open
    fireEvent.click(screen.getByTestId("tool-group-builtin-off"));
    expect(screen.getByTestId("tools-save")).not.toBeDisabled();
    fireEvent.click(header); // open it: the rows carry the fold's choice
    expect(screen.getByTestId("tool-read_file-off")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("tools-save"));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ "rca-tools": false, exec: false, read_file: false, write_file: false }),
    );
  });

  // plan-tools-picker-groups part 2: an item pinned before commands were
  // pickable holds `{"rca-tools": false}`; the server reads that as every
  // command of the package pinned off and hands one `pkg:cmd` row per command,
  // each `pref: "off"`. The override is rebuilt from the rows, so the first
  // Save writes per-command keys and the bare key is gone — without the modal
  // knowing there ever was one.
  it("a legacy whole-package key comes back from Save as one key per command", async () => {
    const onSave = vi.fn();
    const cmd = (c: string): ItemToolState => ({
      key: `rca-tools:${c}`,
      group: "rca-tools",
      package: "RCA Tools",
      label: c,
      description: `${c}.`,
      default_on: true,
      pref: "off",
      effective: false,
    });
    const rows: ItemToolState[] = [TOOLS[0]!, cmd("spc"), cmd("pareto"), cmd("wafer-history")];
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={onSave} onClose={vi.fn()} client={fakeClient(rows)} />,
    );
    const header = await screen.findByTestId("tool-group-header-rca-tools");
    fireEvent.click(header);
    fireEvent.click(screen.getByTestId("tool-rca-tools:spc-on")); // turn one command back on
    fireEvent.click(screen.getByTestId("tools-save"));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        "rca-tools:spc": true,
        "rca-tools:pareto": false,
        "rca-tools:wafer-history": false,
      }),
    );
    expect(onSave.mock.calls[0]![0]).not.toHaveProperty("rca-tools");
  });

  it("a clean cancel closes immediately (no discard prompt)", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={vi.fn()} onClose={onClose} client={fakeClient()} />,
    );
    fireEvent.click(await screen.findByTestId("tools-cancel"));
    expect(onClose).toHaveBeenCalled();
    expect(screen.queryByTestId("dialog-action-discard")).toBeNull();
  });

  // #779: the guard existed before but nothing covered it, so a regression here
  // would have been silent — and this is the modal where losing the answer means
  // re-picking every tool.
  it("asks before dropping unsaved tool choices, and only closes once confirmed", async () => {
    const onClose = vi.fn();
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={vi.fn()} onClose={onClose} client={fakeClient()} />,
    );
    fireEvent.click(await screen.findByTestId("tool-exec-off")); // pin exec off = dirty
    fireEvent.click(screen.getByTestId("tools-cancel"));

    expect(onClose).not.toHaveBeenCalled();
    await screen.findByTestId("dialog-action-discard");
    fireEvent.click(screen.getByTestId("dialog-action-discard"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

// plan-tool-running-version: the live sandbox may run an older release than
// the row's version (the latest). The row says which; the modal says closing
// the sandbox is what updates it, with a button for whoever may close it.
describe("ToolsPickerModal — a sandbox older than the release", () => {
  const OUTDATED: ItemToolState[] = [
    ...TOOLS,
    {
      key: "wafer-history:trend",
      group: "wafer-history",
      label: "Trend",
      description: "Yield trend.",
      default_on: true,
      pref: "follow",
      effective: true,
      external: true,
      version: "1.4.2",
      running_version: "1.3.0",
    },
  ];

  it("names the release that runs beside the latest on its row", async () => {
    renderWithQuery(
      <ToolsPickerModal
        slug="rca"
        itemId="i1"
        onSave={vi.fn()}
        onClose={vi.fn()}
        client={fakeClient(OUTDATED, { updateNeedsClose: true, canClose: true })}
        closeClient={{ closeEnvironment: vi.fn(async () => undefined) }}
      />,
    );
    const running = await screen.findByTestId("tool-wafer-history:trend-running");
    expect(running).toHaveTextContent("1.3.0");
    expect(running).toHaveTextContent("1.4.2");
  });

  it("closes the item's sandbox from one button, then reads the picker again", async () => {
    const closeEnvironment = vi.fn(async () => undefined);
    const client = fakeClient(OUTDATED, { updateNeedsClose: true, canClose: true });
    renderWithQuery(
      <ToolsPickerModal
        slug="rca"
        itemId="i1"
        onSave={vi.fn()}
        onClose={vi.fn()}
        client={client}
        closeClient={{ closeEnvironment }}
      />,
    );
    expect(await screen.findByTestId("tools-update-note")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tools-update-close"));
    await waitFor(() => expect(closeEnvironment).toHaveBeenCalledWith("i1"));
    await waitFor(() => expect(client.getItemTools).toHaveBeenCalledTimes(2));
  });

  it("tells someone who may not close it, without a button that would do nothing", async () => {
    renderWithQuery(
      <ToolsPickerModal
        slug="rca"
        itemId="i1"
        onSave={vi.fn()}
        onClose={vi.fn()}
        client={fakeClient(OUTDATED, { updateNeedsClose: true, canClose: false })}
        closeClient={{ closeEnvironment: vi.fn(async () => undefined) }}
      />,
    );
    expect(await screen.findByTestId("tools-update-note")).toBeInTheDocument();
    expect(screen.queryByTestId("tools-update-close")).not.toBeInTheDocument();
  });

  it("says nothing when the sandbox runs the latest", async () => {
    renderWithQuery(
      <ToolsPickerModal slug="rca" itemId="i1" onSave={vi.fn()} onClose={vi.fn()} client={fakeClient()} />,
    );
    await screen.findByTestId("tools-save");
    expect(screen.queryByTestId("tools-update-note")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tools-update-close")).not.toBeInTheDocument();
  });
});
