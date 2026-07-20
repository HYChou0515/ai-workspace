// @vitest-environment happy-dom
/**
 * #520: the shipped-template section of the Workflows panel. The behaviours worth
 * pinning are the two that protect the user's own work: an unusable template is shown
 * (not hidden) with the reason, and a name clash asks before replacing.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { renderWithQuery } from "../test/queryWrapper";
import { DialogProvider } from "./Dialog";
import { WorkflowsModal } from "./WorkflowsModal";

const listWorkflowsMock = vi.fn();
vi.mock("../api/workspaceWorkflows", async (orig) => {
  const actual = await orig<typeof import("../api/workspaceWorkflows")>();
  return {
    ...actual,
    workspaceWorkflowsApi: { list: (...a: unknown[]) => listWorkflowsMock(...a) },
  };
});

const listTemplatesMock = vi.fn();
const copyTemplateMock = vi.fn();
vi.mock("../api/workflowTemplates", async (orig) => {
  const actual = await orig<typeof import("../api/workflowTemplates")>();
  return {
    ...actual,
    workflowTemplatesApi: {
      list: (...a: unknown[]) => listTemplatesMock(...a),
      copy: (...a: unknown[]) => copyTemplateMock(...a),
    },
  };
});

vi.mock("../api/workflows", () => ({ workflowApi: { startRun: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  listWorkflowsMock.mockReset();
  listTemplatesMock.mockReset();
  copyTemplateMock.mockReset();
});

function tpl(over: Record<string, unknown> = {}) {
  return {
    id: "image-to-knowledge",
    title: "Image → document + card",
    description: "Turn an upload into knowledge",
    tag: "batch",
    hint: "",
    phases: [{ id: "read", title: "Read" }],
    compatible: true,
    problems: [],
    ...over,
  };
}

function render() {
  const svc = { scopeId: "inv1" } as unknown as FileService;
  return renderWithQuery(
    <DialogProvider>
      <WorkflowsModal slug="playground" itemId="inv1" fileService={svc} onClose={() => {}} />
    </DialogProvider>,
  );
}

describe("WorkflowsModal templates", () => {
  it("offers each shipped template", async () => {
    listWorkflowsMock.mockResolvedValue([]);
    listTemplatesMock.mockResolvedValue([tpl()]);
    render();
    expect(await screen.findByText("Image → document + card")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-template-copy-image-to-knowledge")).toBeEnabled();
  });

  it("shows an unusable template with the reason instead of hiding it", async () => {
    listWorkflowsMock.mockResolvedValue([]);
    listTemplatesMock.mockResolvedValue([
      tpl({ compatible: false, problems: ["tool 'read_image' is outside the profile's allowed tools"] }),
    ]);
    render();
    const btn = await screen.findByTestId("workflow-template-copy-image-to-knowledge");
    // still listed, but inert — and the blocker is available, not swallowed
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", expect.stringContaining("read_image"));
  });

  it("copies without asking to overwrite when the name is free", async () => {
    listWorkflowsMock.mockResolvedValue([]);
    listTemplatesMock.mockResolvedValue([tpl()]);
    copyTemplateMock.mockResolvedValue({ workflow_id: "image-to-knowledge", path: "/p" });
    render();
    fireEvent.click(await screen.findByTestId("workflow-template-copy-image-to-knowledge"));
    await waitFor(() => expect(copyTemplateMock).toHaveBeenCalledTimes(1));
    expect(copyTemplateMock.mock.calls[0][3]).toBeUndefined(); // no overwrite flag
  });

  it("asks before replacing a workflow the user may have edited", async () => {
    const { TemplateConflictError } = await import("../api/workflowTemplates");
    listWorkflowsMock.mockResolvedValue([]);
    listTemplatesMock.mockResolvedValue([tpl()]);
    copyTemplateMock
      .mockRejectedValueOnce(new TemplateConflictError("taken"))
      .mockResolvedValueOnce({ workflow_id: "image-to-knowledge", path: "/p" });
    render();
    fireEvent.click(await screen.findByTestId("workflow-template-copy-image-to-knowledge"));

    // the panel asks first — nothing is replaced until the user says so
    fireEvent.click(await screen.findByRole("button", { name: "覆蓋" }));

    await waitFor(() => expect(copyTemplateMock).toHaveBeenCalledTimes(2));
    expect(copyTemplateMock.mock.calls[1][3]).toEqual({ overwrite: true });
  });

  it("keeps the user's version when they decline the replacement", async () => {
    const { TemplateConflictError } = await import("../api/workflowTemplates");
    listWorkflowsMock.mockResolvedValue([]);
    listTemplatesMock.mockResolvedValue([tpl()]);
    copyTemplateMock.mockRejectedValueOnce(new TemplateConflictError("taken"));

    render();
    fireEvent.click(await screen.findByTestId("workflow-template-copy-image-to-knowledge"));
    fireEvent.click(await screen.findByRole("button", { name: "關閉" }));

    // declining must not fall through to an overwrite
    await waitFor(() => expect(copyTemplateMock).toHaveBeenCalledTimes(1));
    expect(copyTemplateMock).toHaveBeenCalledTimes(1);
  });
});
