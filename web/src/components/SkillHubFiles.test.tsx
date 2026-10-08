// @vitest-environment happy-dom
/**
 * A skill's files (plan-skill-hub-ux-redo D12): one summary line, then the
 * files as a tree whose folders start closed, each file opening its text —
 * the shape that holds 985 files (the audit's 15,204px of pills) as well as
 * three.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SkillHubApi, SkillHubFile } from "../api/skillHub";
import { formatBytes } from "../lib/bytes";
import { translate } from "../lib/i18n";
import { QueryWrap } from "../test/queryWrapper";
import { fakeSkillHub } from "../test/skillHubFake";
import { SkillHubFiles } from "./SkillHubFiles";

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

afterEach(cleanup);

const FILES: SkillHubFile[] = [
  { path: "SKILL.md", size: 1000 },
  { path: "references/a.md", size: 200 },
  { path: "references/deep/b.md", size: 300 },
  { path: "scripts/run.py", size: 50 },
  { path: "shot.png", size: 2048 },
];

function mount(files: SkillHubFile[], scripts: number, client: SkillHubApi = fakeSkillHub()) {
  return render(
    <QueryWrap>
      <SkillHubFiles entryId="e-1" revision="e-1:3" files={files} scripts={scripts} client={client} />
    </QueryWrap>,
  );
}

describe("SkillHubFiles", () => {
  it("sums the files up in one line: how many, how big, how many scripts", () => {
    mount(FILES, 1);
    expect(
      screen.getByText(
        word("skillHub.files.summary", { count: 5, size: formatBytes(3598) }),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(word("skillHub.files.scripts", { count: 1 }))).toBeInTheDocument();
  });

  it("says how many files but no size when a size is unknown — never 0 B", () => {
    mount([{ path: "SKILL.md", size: null }, { path: "a.md", size: null }], 0);
    expect(screen.getByText(word("skillHub.files.count", { count: 2 }))).toBeInTheDocument();
    expect(screen.queryByText(/0 B/)).toBeNull();
  });

  it("says nothing about scripts when there are none", () => {
    mount([{ path: "SKILL.md", size: 10 }], 0);
    expect(screen.queryByText(/script/)).toBeNull();
  });

  it("starts with every folder closed, and opens one on request", () => {
    mount(FILES, 1);
    const tree = screen.getByRole("list", { name: word("skillHub.files") });
    // Top level only: the root files and the folders.
    expect(within(tree).getByText("SKILL.md")).toBeInTheDocument();
    const refs = within(tree).getByRole("button", { name: /references/ });
    expect(refs).toHaveAttribute("aria-expanded", "false");
    expect(within(tree).queryByText("a.md")).toBeNull();

    fireEvent.click(refs);

    expect(refs).toHaveAttribute("aria-expanded", "true");
    expect(within(tree).getByText("a.md")).toBeInTheDocument();
    // a nested folder is closed too
    expect(within(tree).getByRole("button", { name: /deep/ })).toHaveAttribute("aria-expanded", "false");
    expect(within(tree).queryByText("b.md")).toBeNull();
  });

  it("opens a file's text, and says so for a file that is not text", async () => {
    const c = fakeSkillHub();
    c.versionFile.mockImplementation(async (_e, _r, path) =>
      path === "shot.png" ? { path, text: null, size: 2048 } : { path, text: "# hello", size: 7 },
    );
    mount(FILES, 1, c);

    fireEvent.click(screen.getByRole("button", { name: /SKILL\.md/ }));
    expect(await screen.findByText("# hello")).toBeInTheDocument();
    expect(c.versionFile).toHaveBeenCalledWith("e-1", "e-1:3", "SKILL.md");

    fireEvent.click(screen.getByRole("button", { name: /shot\.png/ }));
    expect(await screen.findByText(word("skillHub.files.notText"))).toBeInTheDocument();
  });

  it("lists the files without opening any when the version cannot be read file by file", () => {
    mount(FILES, 1);
    cleanup();
    render(
      <QueryWrap>
        <SkillHubFiles entryId="e-1" revision="" files={FILES} scripts={1} client={fakeSkillHub()} />
      </QueryWrap>,
    );
    expect(screen.queryByRole("button", { name: /SKILL\.md/ })).toBeNull();
    expect(screen.getByText("SKILL.md")).toBeInTheDocument();
  });

  it("draws 985 files as a handful of closed folders", () => {
    const many: SkillHubFile[] = [
      { path: "SKILL.md", size: 1 },
      ...Array.from({ length: 984 }, (_, n) => ({ path: `references/r${n}.md`, size: 1 })),
    ];
    mount(many, 0);
    const tree = screen.getByRole("list", { name: word("skillHub.files") });
    expect(within(tree).getAllByRole("listitem")).toHaveLength(2);
  });
});
