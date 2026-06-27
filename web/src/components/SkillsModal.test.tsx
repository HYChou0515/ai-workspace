// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import { renderWithQuery } from "../test/queryWrapper";
import { SkillsModal } from "./SkillsModal";

// Stub the listing API; keep skillDir() real (the modal derives the download prefix
// from it).
const listMock = vi.fn();
vi.mock("../api/workspaceSkills", async (orig) => {
  const actual = await orig<typeof import("../api/workspaceSkills")>();
  return { ...actual, workspaceSkillsApi: { list: (...a: unknown[]) => listMock(...a) } };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  listMock.mockReset();
});

function fakeService() {
  const writes: { path: string }[] = [];
  const prepareDirDownload = vi.fn(async () => ({
    download_id: "d1",
    filename: "f.zip",
    size: 9,
  }));
  const dirDownloadUrl = vi.fn((id: string, prefix: string) => `/dl/${id}?p=${prefix}`);
  const writeFile = vi.fn(async (path: string) => {
    writes.push({ path });
  });
  const svc = {
    scopeId: "inv1",
    prepareDirDownload,
    dirDownloadUrl,
    writeFile,
  } as unknown as FileService;
  return { svc, prepareDirDownload, dirDownloadUrl, writeFile, writes };
}

function render(svc: FileService) {
  return renderWithQuery(
    <SkillsModal slug="playground" itemId="inv1" fileService={svc} onClose={() => {}} />,
  );
}

describe("SkillsModal", () => {
  it("lists the workspace's skills with names + descriptions", async () => {
    listMock.mockResolvedValue([
      { name: "alpha", description: "does A" },
      { name: "beta", description: "does B" },
    ]);
    const { svc } = fakeService();
    render(svc);
    expect(await screen.findByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("does A")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("shows an empty state pointing at the assistant when there are no skills", async () => {
    listMock.mockResolvedValue([]);
    const { svc } = fakeService();
    render(svc);
    expect(await screen.findByTestId("skills-empty")).toBeInTheDocument();
  });

  it("downloads a skill as its `.skill/<name>` folder zip", async () => {
    listMock.mockResolvedValue([{ name: "alpha", description: "does A" }]);
    const f = fakeService();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(f.svc);
    fireEvent.click(await screen.findByTestId("skill-download-alpha"));
    await waitFor(() => expect(f.prepareDirDownload).toHaveBeenCalledWith(".skill/alpha"));
    expect(f.dirDownloadUrl).toHaveBeenCalledWith("d1", ".skill/alpha");
    expect(click).toHaveBeenCalled();
  });

  it("imports a selected skill folder into `.skill/<folder>/…`", async () => {
    listMock.mockResolvedValue([]);
    const f = fakeService();
    render(f.svc);
    await screen.findByTestId("skills-empty");
    const file = new File(["body"], "SKILL.md", { type: "text/markdown" });
    Object.defineProperty(file, "webkitRelativePath", { value: "my-skill/SKILL.md" });
    fireEvent.change(screen.getByTestId("skills-import-input"), { target: { files: [file] } });
    await waitFor(() =>
      expect(f.writeFile).toHaveBeenCalledWith(".skill/my-skill/SKILL.md", expect.anything()),
    );
  });
});
