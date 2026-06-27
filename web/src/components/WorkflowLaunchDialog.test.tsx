// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { workflowApi, type PreflightPreviewDTO } from "../api/workflows";
import { renderWithQuery } from "../test/queryWrapper";
import { WorkflowLaunchDialog } from "./WorkflowLaunchDialog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const preview = (over: Partial<PreflightPreviewDTO> = {}): PreflightPreviewDTO => ({
  workflow_id: "collections",
  title: "File uploads into collections",
  description: "Classify & digest each dropped file.",
  phases: [
    { id: "classify", title: "Classify" },
    { id: "commit", title: "Commit" },
  ],
  summary: "把 uploads/ 裡的 3 個檔案歸檔到 Defects。",
  checks: [
    { label: "已設定知識庫", ok: true, severity: "required", reason: "" },
    { label: "uploads/ 內有檔案", ok: true, severity: "required", reason: "" },
  ],
  can_run: true,
  has_preflight: true,
  ...over,
});

describe("WorkflowLaunchDialog", () => {
  it("shows the summary + checklist and confirms when ready", async () => {
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue(preview());
    const onConfirm = vi.fn();
    renderWithQuery(
      <WorkflowLaunchDialog
        slug="topic-hub"
        itemId="i1"
        workflowId="collections"
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    // the human "what it will do" summary is surfaced
    await screen.findByText(/Defects/);
    // each precondition is listed
    expect(screen.getByText(/已設定知識庫/)).toBeInTheDocument();
    const run = screen.getByTestId("wf-launch-run");
    expect(run).toBeEnabled();
    fireEvent.click(run);
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
  });

  it("blocks Run when a required precondition fails and shows its reason", async () => {
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue(
      preview({
        can_run: false,
        summary: "依下方檢查清單，這次執行會空轉。",
        checks: [
          {
            label: "uploads/ 內有檔案",
            ok: false,
            severity: "required",
            reason: "先把檔案放進 uploads/ 再執行。",
          },
        ],
      }),
    );
    const onConfirm = vi.fn();
    renderWithQuery(
      <WorkflowLaunchDialog
        slug="topic-hub"
        itemId="i1"
        workflowId="collections"
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    // wait for the preview to resolve (the blocked banner only renders with data)
    await screen.findByTestId("wf-launch-blocked");
    const run = screen.getByTestId("wf-launch-run");
    expect(run).toBeDisabled();
    expect(screen.getByText(/先把檔案放進 uploads/)).toBeInTheDocument();
    fireEvent.click(run);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("cancels without confirming", async () => {
    vi.spyOn(workflowApi, "previewRun").mockResolvedValue(preview());
    const onClose = vi.fn();
    renderWithQuery(
      <WorkflowLaunchDialog
        slug="topic-hub"
        itemId="i1"
        workflowId="collections"
        onConfirm={() => {}}
        onClose={onClose}
      />,
    );
    const cancel = await screen.findByTestId("wf-launch-cancel");
    fireEvent.click(cancel);
    expect(onClose).toHaveBeenCalled();
  });
});
