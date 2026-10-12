// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../api/fileService";
import { workflowApi, type WorkflowRunDTO } from "../api/workflows";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";
import { ItemProfileProvider } from "../hooks/itemProfile";
import { OpenFileProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import type { ShownFile } from "../renderers/shownFiles";
import { renderWithQuery } from "../test/queryWrapper";
import { ShownFiles } from "./ShownFiles";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(workflowApi, "getRun").mockResolvedValue({
    run_id: "r1",
    item_id: "it",
    workflow_id: "classify",
    status: "done",
    started: new Date(2026, 9, 12, 14, 3).getTime(),
  } as WorkflowRunDTO);
  vi.spyOn(workflowApi, "listProfiles").mockResolvedValue([
    {
      name: "default",
      title: "Default",
      description: "",
      has_workflow: true,
      workflow: null,
      workflows: [{ id: "classify", title: "Classify wafers", phases: [], input_json: "" }],
    },
  ]);
  vi.spyOn(workspaceWorkflowsApi, "list").mockResolvedValue([]);
});

const CARD: ShownFile = {
  path: "workflow-run:r1",
  mime: "application/x-workflow-execution",
  size: 0,
  caption: "last night's run",
};

const render = (openFile = vi.fn(), visible = true) => {
  renderWithQuery(
    <WorkspaceSlugProvider value="hub">
      <FileServiceProvider value={{ scopeId: "it" } as FileService}>
        <ItemProfileProvider value="default">
          <OpenFileProvider value={openFile}>
            <WorkspaceVisibleProvider value={visible}>
              <ShownFiles files={[CARD]} />
            </WorkspaceVisibleProvider>
          </OpenFileProvider>
        </ItemProfileProvider>
      </FileServiceProvider>
    </WorkspaceSlugProvider>,
  );
  return openFile;
};

// plan-workflow-out-of-chat D8: the chat's AI shows an execution with
// `show_file`; the card names it the way its tab does and opens that tab.
describe("an execution's card in the chat", () => {
  it("is named like its tab, carries the caption, and opens the execution", async () => {
    const openFile = render();
    const card = await screen.findByRole("button", { name: /Classify wafers · 10\/12 14:03/ });
    expect(card).toHaveTextContent("last night's run");
    expect(card).not.toHaveTextContent("workflow-run:");
    expect(card).not.toHaveTextContent("0 B");
    fireEvent.click(card);
    expect(openFile).toHaveBeenCalledWith("workflow-run:r1");
  });
});
