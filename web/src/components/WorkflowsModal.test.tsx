// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { renderWithQuery } from "../test/queryWrapper";
import { WorkflowsModal } from "./WorkflowsModal";

// Stub the listing API; keep WORKFLOWS_DIR real (the modal derives the download prefix).
const listMock = vi.fn();
vi.mock("../api/workspaceWorkflows", async (orig) => {
  const actual = await orig<typeof import("../api/workspaceWorkflows")>();
  return { ...actual, workspaceWorkflowsApi: { list: (...a: unknown[]) => listMock(...a) } };
});

const startRunMock = vi.fn();
vi.mock("../api/workflows", () => ({
  workflowApi: { startRun: (...a: unknown[]) => startRunMock(...a) },
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  listMock.mockReset();
  startRunMock.mockReset();
});

function fakeService() {
  const writes: { path: string }[] = [];
  const prepareDirDownload = vi.fn(async () => ({ download_id: "d1", filename: "f.zip", size: 9 }));
  const dirDownloadUrl = vi.fn((id: string, prefix: string) => `/dl/${id}?p=${prefix}`);
  const writeFile = vi.fn(async (path: string) => {
    writes.push({ path });
  });
  const svc = { scopeId: "inv1", prepareDirDownload, dirDownloadUrl, writeFile } as unknown as FileService;
  return { svc, prepareDirDownload, dirDownloadUrl, writeFile, writes };
}

function render(svc: FileService, onRun?: (chatId: string) => void) {
  return renderWithQuery(
    <WorkflowsModal
      slug="playground"
      itemId="inv1"
      fileService={svc}
      onClose={() => {}}
      onRun={onRun}
    />,
  );
}

describe("WorkflowsModal", () => {
  it("lists the workspace's workflows with title + step count", async () => {
    listMock.mockResolvedValue([
      { id: "filer", title: "File uploads", phases: [{ id: "a" }, { id: "b" }] },
      { id: "memo", title: "", phases: [{ id: "x" }] },
    ]);
    render(fakeService().svc);
    expect(await screen.findByText("File uploads")).toBeInTheDocument();
    expect(screen.getByText("2 個步驟")).toBeInTheDocument(); // default test locale is zh-TW
    expect(screen.getByText("memo")).toBeInTheDocument(); // falls back to id when no title
    expect(screen.getByText("1 個步驟")).toBeInTheDocument();
  });

  it("shows the empty state when there are none", async () => {
    listMock.mockResolvedValue([]);
    render(fakeService().svc);
    expect(await screen.findByTestId("workflows-empty")).toBeInTheDocument();
  });

  it("runs a workflow → startRun, then onRun + close", async () => {
    listMock.mockResolvedValue([{ id: "filer", title: "File uploads", phases: [{ id: "a" }] }]);
    startRunMock.mockResolvedValue({ run_id: "r1", item_id: "inv1", chat_id: "c9" });
    const onRun = vi.fn();
    render(fakeService().svc, onRun);
    fireEvent.click(await screen.findByTestId("workflow-run-filer"));
    await waitFor(() => expect(startRunMock).toHaveBeenCalledWith("playground", "inv1", "filer"));
    expect(onRun).toHaveBeenCalledWith("c9");
  });

  it("downloads the whole .workflows folder", async () => {
    listMock.mockResolvedValue([{ id: "filer", title: "F", phases: [] }]);
    const { svc, prepareDirDownload } = fakeService();
    render(svc);
    await screen.findByText("F"); // wait for the list so the download button is enabled
    fireEvent.click(screen.getByTestId("workflows-download"));
    await waitFor(() => expect(prepareDirDownload).toHaveBeenCalledWith(".workflows"));
  });

  it("imports a .json file into .workflows/", async () => {
    listMock.mockResolvedValue([]);
    const { svc, writeFile } = fakeService();
    render(svc);
    const input = (await screen.findByTestId("workflows-import-input")) as HTMLInputElement;
    const file = new File(['{"id":"x"}'], "flow.json", { type: "application/json" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(writeFile).toHaveBeenCalledWith(".workflows/flow.json", expect.anything()));
  });
});
