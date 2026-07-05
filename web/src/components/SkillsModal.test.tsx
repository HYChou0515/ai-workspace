// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import type { ItemSkillState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { SkillsModal } from "./SkillsModal";

// #380: the Skills panel lists every available skill (all three sources) with a
// persistent tri-state toggle (attached_skill_prefs), a one-shot "apply this turn"
// button, plus the workspace-skill download + folder import it always had.

const SKILLS: ItemSkillState[] = [
  {
    name: "author-skill",
    description: "co-author a skill",
    source: "shared",
    default_on: true,
    pref: "follow",
    effective: true,
  },
  {
    name: "designed-pptx",
    description: "polished slides",
    source: "shared",
    default_on: false,
    pref: "off",
    effective: false,
  },
  {
    name: "my-skill",
    description: "mine",
    source: "workspace",
    default_on: true,
    pref: "follow",
    effective: true,
  },
];

function fakeClient(skills = SKILLS) {
  return { getItemSkills: vi.fn(async () => skills) };
}

function fakeService() {
  const prepareDirDownload = vi.fn(async () => ({ download_id: "d1", filename: "f.zip", size: 9 }));
  const dirDownloadUrl = vi.fn((id: string, prefix: string) => `/dl/${id}?p=${prefix}`);
  const writeFile = vi.fn(async () => {});
  const svc = {
    scopeId: "inv1",
    prepareDirDownload,
    dirDownloadUrl,
    writeFile,
  } as unknown as FileService;
  return { svc, prepareDirDownload, dirDownloadUrl, writeFile };
}

function renderModal(
  overrides: Partial<ComponentProps<typeof SkillsModal>> = {},
): ComponentProps<typeof SkillsModal> {
  const props: ComponentProps<typeof SkillsModal> = {
    slug: "rca",
    itemId: "i1",
    fileService: fakeService().svc,
    onClose: vi.fn(),
    onSaveSkillPrefs: vi.fn(),
    appliedSkills: [],
    onToggleApply: vi.fn(),
    client: fakeClient(),
    ...overrides,
  };
  renderWithQuery(<SkillsModal {...props} />);
  return props;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SkillsModal (#380)", () => {
  it("lists skills across all sources with a source badge", async () => {
    renderModal();
    expect(await screen.findByTestId("skill-author-skill-follow")).toBeInTheDocument();
    expect(screen.getByTestId("skill-designed-pptx-off")).toBeInTheDocument();
    expect(screen.getByTestId("skill-my-skill-follow")).toBeInTheDocument();
    expect(screen.getByTestId("skill-source-my-skill")).toHaveTextContent("workspace");
  });

  it("exposes each skill's full description via title= so a clipped line is readable on hover (#456)", async () => {
    renderModal();
    expect(await screen.findByText("co-author a skill")).toHaveAttribute(
      "title",
      "co-author a skill",
    );
  });

  it("seeds the tri-state from the server-resolved pref", async () => {
    renderModal();
    expect(await screen.findByTestId("skill-designed-pptx-off")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("skill-author-skill-follow")).toHaveAttribute("aria-pressed", "true");
  });

  it("Save persists only the sparse override", async () => {
    const onSaveSkillPrefs = vi.fn();
    renderModal({ onSaveSkillPrefs });
    fireEvent.click(await screen.findByTestId("skill-author-skill-off"));
    fireEvent.click(screen.getByTestId("skills-save"));
    // The saved override carries the pre-existing off pin (designed-pptx) plus the
    // new one — the sparse dict, no "follow" entries.
    await waitFor(() =>
      expect(onSaveSkillPrefs).toHaveBeenCalledWith({
        "designed-pptx": false,
        "author-skill": false,
      }),
    );
  });

  it("apply button toggles the skill for this turn", async () => {
    const onToggleApply = vi.fn();
    renderModal({ onToggleApply });
    fireEvent.click(await screen.findByTestId("skill-apply-my-skill"));
    expect(onToggleApply).toHaveBeenCalledWith("my-skill");
  });

  it("marks an already-applied skill's apply button active", async () => {
    renderModal({ appliedSkills: ["my-skill"] });
    expect(await screen.findByTestId("skill-apply-my-skill")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("downloads a workspace skill as its `.skill/<name>` folder zip", async () => {
    const f = fakeService();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderModal({ fileService: f.svc });
    fireEvent.click(await screen.findByTestId("skill-download-my-skill"));
    await waitFor(() => expect(f.prepareDirDownload).toHaveBeenCalledWith(".skill/my-skill"));
    expect(f.dirDownloadUrl).toHaveBeenCalledWith("d1", ".skill/my-skill");
    expect(click).toHaveBeenCalled();
  });

  it("offers download only on workspace skills", async () => {
    renderModal();
    expect(await screen.findByTestId("skill-download-my-skill")).toBeInTheDocument();
    expect(screen.queryByTestId("skill-download-author-skill")).toBeNull();
  });

  it("imports a selected skill folder into `.skill/<folder>/…`", async () => {
    const f = fakeService();
    renderModal({ fileService: f.svc });
    await screen.findByTestId("skills-import");
    const file = new File(["body"], "SKILL.md", { type: "text/markdown" });
    Object.defineProperty(file, "webkitRelativePath", { value: "new-skill/SKILL.md" });
    fireEvent.change(screen.getByTestId("skills-import-input"), { target: { files: [file] } });
    await waitFor(() =>
      expect(f.writeFile).toHaveBeenCalledWith(".skill/new-skill/SKILL.md", expect.anything()),
    );
  });
});
