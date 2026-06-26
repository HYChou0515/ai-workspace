// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

function stubAgent(): AgentState {
  return {
    investigationId: "it1",
    log: { entries: [], streaming: false } as unknown as AgentState["log"],
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function renderPanel(uploadDir = "uploads") {
  return renderWithQuery(
    <DialogProvider>
      <AgentPanel
        investigationId="it1"
        agent={stubAgent()}
        picker={[]}
        suggestions={[]}
        attachedPreset=""
        onAttachPreset={() => {}}
        uploadDir={uploadDir}
      />
    </DialogProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  // #245: the composer's UsageBar queries this; stub so it doesn't hit the network.
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentPanel attach (#198)", () => {
  it("stages a dropped file into the profile's upload_dir and injects its path", async () => {
    const upload = vi.spyOn(api, "uploadFile").mockResolvedValue();
    const { container } = renderPanel("dropbox");

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "report.csv")] } });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith(
        "", // useWorkspaceSlug default in tests (no provider)
        "it1",
        "/dropbox/report.csv",
        expect.any(File),
        expect.anything(),
      ),
    );
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toContain("/dropbox/report.csv"));
  });

  it("attaches a non-text, larger-than-256KB file (the old gate is gone)", async () => {
    const upload = vi.spyOn(api, "uploadFile").mockResolvedValue();
    const { container } = renderPanel();
    const big = new File([new Uint8Array(512 * 1024)], "scan.bin");

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [big] } });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith(
        "",
        "it1",
        "/uploads/scan.bin",
        expect.any(File),
        expect.anything(),
      ),
    );
  });

  it("alerts but keeps the survivors when one file is too large (413)", async () => {
    const alertSpy = vi.fn();
    vi.stubGlobal("alert", alertSpy);
    vi.spyOn(api, "uploadFile").mockImplementation(async (_s, _i, path) => {
      if (path === "/uploads/big.bin") throw Object.assign(new Error("413"), { status: 413 });
    });
    const { container } = renderPanel();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "big.bin"), new File(["y"], "ok.csv")] },
    });

    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toContain("/uploads/ok.csv"));
    expect(composer.value).not.toContain("big.bin");
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0][0]).toContain("size limit");
  });
});
