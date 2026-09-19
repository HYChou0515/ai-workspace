// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import type { ItemSkillState } from "../api/types";
import { HttpError } from "../api/http";
import { makeQueryClient } from "../api/queryClient";
import { currentWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import type { SkillHubCard } from "../api/skillHub";
import { subscribeAgentDraft } from "../lib/agentDraftBus";
import { translate } from "../lib/i18n";
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
  return {
    getItemSkills: vi.fn(async () => skills),
    refreshItemSkill: vi.fn(async () => ({
      updated: [],
      skipped: [],
      removed: [],
    })),
  };
}

function fakeService() {
  const prepareDirDownload = vi.fn(async () => ({
    download_id: "d1",
    filename: "f.zip",
    size: 9,
  }));
  const dirDownloadUrl = vi.fn(
    (id: string, prefix: string) => `/dl/${id}?p=${prefix}`,
  );
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
    expect(
      await screen.findByTestId("skill-author-skill-follow"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("skill-designed-pptx-off")).toBeInTheDocument();
    expect(screen.getByTestId("skill-my-skill-follow")).toBeInTheDocument();
    expect(screen.getByTestId("skill-source-my-skill")).toHaveTextContent(
      "workspace",
    );
  });

  it("keeps a row's controls in ONE cluster, so a narrow panel wraps them under the text as a unit", async () => {
    // Measured in Chromium, not here (happy-dom lays nothing out): at 390 px
    // the controls used to fight the text for one line — pills over buttons,
    // toggles two lines high, the name clipped. The row wraps its cluster
    // now, and the cluster wraps within itself; what a DOM test can hold is
    // the structure that makes that possible: every control of a workspace
    // skill's row is inside the one cluster element, none beside it.
    renderModal();
    const row = await screen.findByTestId("skill-row-my-skill");
    const cluster = within(row).getByTestId("skill-actions-my-skill");
    const controls = within(row).getAllByRole("button");
    expect(controls.length).toBeGreaterThanOrEqual(5); // apply, download, three toggles
    for (const c of controls) expect(cluster.contains(c)).toBe(true);
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
    expect(
      await screen.findByTestId("skill-designed-pptx-off"),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("skill-author-skill-follow")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
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
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    renderModal({ fileService: f.svc });
    fireEvent.click(await screen.findByTestId("skill-download-my-skill"));
    await waitFor(() =>
      expect(f.prepareDirDownload).toHaveBeenCalledWith(".skill/my-skill"),
    );
    expect(f.dirDownloadUrl).toHaveBeenCalledWith("d1", ".skill/my-skill");
    expect(click).toHaveBeenCalled();
  });

  it("offers download only on workspace skills", async () => {
    renderModal();
    expect(
      await screen.findByTestId("skill-download-my-skill"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("skill-download-author-skill")).toBeNull();
  });

  it("imports a selected skill folder into `.skill/<folder>/…`", async () => {
    const f = fakeService();
    renderModal({ fileService: f.svc });
    await screen.findByTestId("skills-import");
    const file = new File(["body"], "SKILL.md", { type: "text/markdown" });
    Object.defineProperty(file, "webkitRelativePath", {
      value: "new-skill/SKILL.md",
    });
    fireEvent.change(screen.getByTestId("skills-import-input"), {
      target: { files: [file] },
    });
    await waitFor(() =>
      expect(f.writeFile).toHaveBeenCalledWith(
        ".skill/new-skill/SKILL.md",
        expect.anything(),
      ),
    );
  });
});

// #589: a baked-in skill that ships scripts is COPIED into the workspace. The row
// still reports its package source — so using a default-off one cannot quietly
// turn it on for good — but its files really are here. Keying the download
// control off `source === "workspace"` would therefore hide it for exactly the
// skills that just gained downloadable files.
describe("SkillsModal — a baked-in skill with a local copy (#589)", () => {
  const COPIED: ItemSkillState[] = [
    {
      name: "triage",
      description: "triage a defect",
      source: "profile",
      default_on: true,
      is_copy: true,
      pref: "follow",
      effective: true,
    },
  ];

  it("offers download for a copy even though its source is not `workspace`", async () => {
    renderModal({ client: fakeClient(COPIED) as never });
    expect(
      await screen.findByTestId("skill-download-triage"),
    ).toBeInTheDocument();
  });

  it("says the copy is editable here, so its source badge is not the whole story", async () => {
    renderModal({ client: fakeClient(COPIED) as never });
    expect(await screen.findByTestId("skill-copy-triage")).toBeInTheDocument();
  });
});

// The copy is frozen at the version it was made from — deliberately, so the AI's
// edits survive. That makes an explicit way to pull a newer version the other
// half of the bargain: without it, "frozen" is just "stuck".
describe("SkillsModal — refreshing a copy (#589)", () => {
  const COPIED: ItemSkillState[] = [
    {
      name: "triage",
      description: "triage a defect",
      source: "profile",
      default_on: true,
      is_copy: true,
      update_available: true,
      pref: "follow",
      effective: true,
    },
  ];

  const NO_UPDATE: ItemSkillState[] = [
    { ...COPIED[0], update_available: false },
  ];

  it("pulls the shipped version and reports what it left alone", async () => {
    const refreshItemSkill = vi.fn(async () => ({
      updated: ["scripts/x.py"],
      skipped: ["scripts/tuned.py"],
      removed: [],
    }));
    renderModal({
      client: { ...fakeClient(COPIED), refreshItemSkill } as never,
    });

    fireEvent.click(await screen.findByTestId("skill-refresh-triage"));

    await waitFor(() =>
      expect(refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "triage", {
        force: false,
      }),
    );
    // The files it did NOT touch are the ones the user needs told about.
    expect(await screen.findByText(/scripts\/tuned\.py/)).toBeInTheDocument();
  });

  it("offers reset-to-factory even when there is nothing new upstream", async () => {
    const refreshItemSkill = vi.fn(async () => ({
      updated: [],
      skipped: [],
      removed: [],
    }));
    renderModal({
      client: { ...fakeClient(NO_UPDATE), refreshItemSkill } as never,
    });

    fireEvent.click(await screen.findByTestId("skill-reset-triage"));

    // The escape hatch for edits the per-file update deliberately refuses to
    // touch: without it, one bad edit by the AI has no way back.
    await waitFor(() =>
      expect(refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "triage", {
        force: true,
      }),
    );
  });

  it("hides the update control when upstream has not moved", async () => {
    renderModal({ client: fakeClient(NO_UPDATE) as never });
    await screen.findByTestId("skill-row-triage");
    // A button whose only honest outcome is "nothing changed" reads as broken.
    expect(screen.queryByTestId("skill-refresh-triage")).toBeNull();
    expect(screen.getByTestId("skill-reset-triage")).toBeInTheDocument();
  });

  it("says 「有新版」 on the row, in words, when upstream has moved (plan-skill-hub-ui-polish D4)", async () => {
    renderModal({
      client: fakeClient([
        ...COPIED,
        ...NO_UPDATE.map((s) => ({ ...s, name: "settled" })),
      ]) as never,
    });
    await screen.findByTestId("skill-row-triage");
    // A fourth unlabelled icon was the only sign; a status is a badge.
    expect(screen.getByTestId("skill-update-triage")).toHaveTextContent(
      word("skills.updateAvailable"),
    );
    expect(screen.queryByTestId("skill-update-settled")).toBeNull();
  });

  it("words Update / Reset and the note by where the copy came from — the package or the hub (D4)", async () => {
    // A hub copy lists as `source: workspace` + `is_copy` (its files never
    // came from the package); a package copy keeps the package's source.
    const skills: ItemSkillState[] = [
      COPIED[0],
      {
        ...COPIED[0],
        name: "from-hub",
        source: "workspace",
        upstream: "live",
      },
    ];
    const refreshItemSkill = vi.fn(async () => ({
      updated: ["SKILL.md"],
      skipped: [],
      removed: [],
    }));
    renderModal({
      client: { ...fakeClient(skills), refreshItemSkill } as never,
    });
    await screen.findByTestId("skill-row-from-hub");

    expect(screen.getByTestId("skill-refresh-triage")).toHaveAccessibleName(
      `${word("skills.refresh")} triage`,
    );
    expect(screen.getByTestId("skill-reset-triage")).toHaveAccessibleName(
      `${word("skills.reset")} triage`,
    );
    expect(screen.getByTestId("skill-refresh-from-hub")).toHaveAccessibleName(
      `${word("skills.refresh.hub")} from-hub`,
    );
    expect(screen.getByTestId("skill-reset-from-hub")).toHaveAccessibleName(
      `${word("skills.reset.hub")} from-hub`,
    );

    fireEvent.click(screen.getByTestId("skill-refresh-from-hub"));
    expect(await screen.findByTestId("skills-refresh-note")).toHaveTextContent(
      word("skills.refreshDone.hub"),
    );
    fireEvent.click(screen.getByTestId("skill-refresh-triage"));
    await waitFor(() =>
      expect(screen.getByTestId("skills-refresh-note")).toHaveTextContent(
        word("skills.refreshDone"),
      ),
    );
  });

  it("offers no refresh for a skill that was written here", async () => {
    renderModal();
    await screen.findByTestId("skill-row-my-skill");
    expect(screen.queryByTestId("skill-refresh-my-skill")).toBeNull();
  });

  // #779: a long list of tri-states — re-picking through it is the cost, and
  // nothing is written until Save.
  it("asks before dropping unsaved skill picks, and keeps them", async () => {
    const props = renderModal();
    fireEvent.click(await screen.findByTestId("skill-author-skill-off"));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByTestId("skill-author-skill-off")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(props.onSaveSkillPrefs).not.toHaveBeenCalled();
  });

  it("closes on Escape without asking when no pick was changed", async () => {
    const props = renderModal();
    await screen.findByTestId("skill-author-skill-follow");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(props.onClose).toHaveBeenCalled();
  });
});

// ── the skill hub's two buttons (docs/plan-skill-hub.md, D2) ─────────────────

const word = (
  key: Parameters<typeof translate>[1],
  vars?: Record<string, string | number>,
) => translate("zh-TW", key, vars);

const hubCard = (over: Partial<SkillHubCard>): SkillHubCard => ({
  id: "e-1",
  owner: "alice",
  name: "triage-reflow",
  description: "Triage reflow defects.",
  source_app: "pm",
  referenced_tools: ["exec", "query_entity"],
  forked_from: "",
  review_verdict: "ok",
  is_mine: false,
  missing_tools: ["query_entity"],
  forks: [],
  ...over,
});

/** The picker links to the skill hub page, so it needs a router under it. */
function renderWithHub(hub: ReturnType<typeof fakeHub>) {
  const props: ComponentProps<typeof SkillsModal> = {
    slug: "rca",
    itemId: "i1",
    fileService: fakeService().svc,
    onClose: vi.fn(),
    onSaveSkillPrefs: vi.fn(),
    appliedSkills: [],
    onToggleApply: vi.fn(),
    client: fakeClient(),
    hubClient: hub,
  };
  renderWithQuery(
    <MemoryRouter>
      <SkillsModal {...props} />
    </MemoryRouter>,
  );
  return props;
}

function fakeHub(rows: SkillHubCard[] = [hubCard({})]) {
  return {
    list: vi.fn(async (_q?: string, _mine?: boolean, _app?: string) => rows),
    install: vi.fn(async (_slug: string, _item: string, _entry: string) => ({
      name: "triage-reflow",
      missing_tools: ["query_entity"],
    })),
  };
}

describe("SkillsModal — the footer at phone width (plan-skill-hub-ui-polish D13)", () => {
  it("keeps the import hint while the footer is unmeasured or wide, and hides it in a measured-narrow footer — the text stays on the Import button", async () => {
    renderModal();
    await screen.findByTestId("skill-row-my-skill");
    // happy-dom lays nothing out: every width is 0 = unmeasured, and an
    // unmeasured footer must not hide anything.
    expect(screen.getByTestId("skills-import-hint")).toHaveTextContent(
      word("skills.importHint"),
    );
    expect(screen.getByTestId("skills-import")).toHaveAttribute(
      "title",
      word("skills.importHint"),
    );
    cleanup();

    const rect = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({
        width: 320,
        height: 24,
        top: 0,
        left: 0,
        right: 320,
        bottom: 24,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
    try {
      renderModal();
      await screen.findByTestId("skill-row-my-skill");
      expect(screen.queryByTestId("skills-import-hint")).toBeNull();
      expect(screen.getByTestId("skills-import")).toHaveAttribute(
        "title",
        word("skills.importHint"),
      );
    } finally {
      rect.mockRestore();
    }
  });
});

describe("SkillsModal — the skill hub", () => {
  it("offers Publish on a workspace skill only, and puts the sentence in the chat box", async () => {
    const props = renderModal();
    await screen.findByTestId("skill-row-my-skill");
    const offered: string[] = [];
    const unsubscribe = subscribeAgentDraft("i1", (text) => offered.push(text));

    // A shared (package) skill's files are the deploy's — nothing to publish.
    expect(screen.queryByTestId("skill-publish-author-skill")).toBeNull();
    fireEvent.click(screen.getByTestId("skill-publish-my-skill"));

    expect(offered).toEqual([
      word("skills.publishSentence", { name: "my-skill" }),
    ]);
    // Offered, not sent — and the panel gets out of the way of the box.
    expect(props.onClose).toHaveBeenCalled();
    unsubscribe();
  });

  it("opens the picker with this App's tool告知 on each row, and installs through the panel's door", async () => {
    const hub = fakeHub();
    renderWithHub(hub);
    await screen.findByTestId("skill-row-my-skill");

    fireEvent.click(screen.getByTestId("skills-from-hub"));
    const picker = await screen.findByTestId("skill-hub-picker");
    await waitFor(() =>
      expect(hub.list).toHaveBeenCalledWith("", false, "rca"),
    );
    expect(await screen.findByTestId("pick-missing-e-1")).toHaveTextContent(
      word("skills.fromHub.missing", { tools: "query_entity" }),
    );

    fireEvent.click(screen.getByTestId("pick-install-e-1"));

    await waitFor(() =>
      expect(hub.install).toHaveBeenCalledWith("rca", "i1", "e-1"),
    );
    await waitFor(() => expect(picker).not.toBeInTheDocument());
    expect(screen.getByTestId("skills-refresh-note")).toHaveTextContent(
      word("skills.fromHub.installed", { name: "triage-reflow" }),
    );
  });

  it("shows the server's refusal when a folder of that name is already here", async () => {
    const hub = fakeHub();
    // What the REAL client throws (see `api/skillHub.test.ts`): an `HttpError`
    // whose message is the server's sentence — not a bare Error carrying it.
    hub.install.mockRejectedValueOnce(
      new HttpError(
        409,
        "this workspace already has alice's '.skill/triage-reflow/' — remove or rename that folder first, then install again",
      ),
    );
    renderWithHub(hub);
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));
    fireEvent.click(await screen.findByTestId("pick-install-e-1"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("alice's '.skill/triage-reflow/'");
    expect(screen.getByTestId("skill-hub-picker")).toBeInTheDocument();
  });

  it("words a coded refusal in the viewer's language (D16)", async () => {
    const hub = fakeHub();
    hub.install.mockRejectedValueOnce(
      new HttpError(
        409,
        "install failed (409)",
        "folder_in_the_way",
        undefined,
        {
          error: "folder_in_the_way",
          owner: "alice",
          path: ".skill/triage-reflow/",
        },
      ),
    );
    renderWithHub(hub);
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));
    fireEvent.click(await screen.findByTestId("pick-install-e-1"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      word("skillHub.refused.folder_in_the_way.theirs", {
        owner: "alice",
        path: ".skill/triage-reflow/",
      }),
    );
  });

  it("reports a refused install ONCE — in the picker, not also as the global write-failure toast (plan-skill-hub-ui-polish D3)", async () => {
    // Mounted on the REAL query client: its MutationCache reports every
    // failed mutation as 「儲存失敗，內容未套用」 unless the mutation says it
    // handles its own error (`meta.silentError`). The picker does — the
    // demo showed both at once, the toast with a title that was not even
    // true (nothing was being saved).
    resetWriteFailures();
    const hub = fakeHub();
    hub.install.mockRejectedValueOnce(
      new HttpError(
        409,
        "this workspace already has alice's '.skill/triage-reflow/' — remove or rename that folder first, then install again",
      ),
    );
    const props: ComponentProps<typeof SkillsModal> = {
      slug: "rca",
      itemId: "i1",
      fileService: fakeService().svc,
      onClose: vi.fn(),
      onSaveSkillPrefs: vi.fn(),
      appliedSkills: [],
      onToggleApply: vi.fn(),
      client: fakeClient(),
      hubClient: hub,
    };
    renderWithQuery(
      <MemoryRouter>
        <SkillsModal {...props} />
      </MemoryRouter>,
      makeQueryClient(),
    );
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));
    fireEvent.click(await screen.findByTestId("pick-install-e-1"));

    await screen.findByRole("alert");
    expect(currentWriteFailure()).toBeNull();
  });

  it("says 「安裝」, names the missing tool before the consequence, and keeps the browse link on its own line (plan-skill-hub-ui-polish D6)", async () => {
    renderWithHub(fakeHub());
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));

    expect(await screen.findByTestId("pick-install-e-1")).toHaveTextContent(
      /^安裝$/,
    );
    expect(screen.getByTestId("pick-missing-e-1")).toHaveTextContent(
      /^缺少 tool：query_entity，/,
    );
    const intro = screen.getByText(word("skills.fromHub.intro"));
    const browse = screen.getByRole("link", {
      name: word("skills.fromHub.browse"),
    });
    // Two lines, not one paragraph with a link glued to its last sentence.
    expect(intro).not.toContainElement(browse);
  });

  it("marks an entry whose name a skill WITH FILES HERE already holds, and disables its Install (D8)", async () => {
    // The install route refuses exactly when `.skill/<name>/` is occupied —
    // a hand-written skill, a hub copy, a copy of a package skill — and
    // accepts a name only a package skill holds (no folder here). The
    // picker marks the same set, from the same listing the panel shows.
    const skills: ItemSkillState[] = [
      ...SKILLS,
      {
        name: "designed-pptx-copy",
        description: "a package skill, copied here",
        source: "shared",
        default_on: false,
        is_copy: true,
        pref: "follow",
        effective: false,
      },
    ];
    const hub = fakeHub([
      hubCard({ id: "e-1", name: "triage-reflow" }),
      hubCard({ id: "e-2", name: "my-skill", missing_tools: [] }),
      hubCard({ id: "e-3", name: "designed-pptx-copy", missing_tools: [] }),
      hubCard({ id: "e-4", name: "author-skill", missing_tools: [] }),
    ]);
    const props: ComponentProps<typeof SkillsModal> = {
      slug: "rca",
      itemId: "i1",
      fileService: fakeService().svc,
      onClose: vi.fn(),
      onSaveSkillPrefs: vi.fn(),
      appliedSkills: [],
      onToggleApply: vi.fn(),
      client: fakeClient(skills),
      hubClient: hub,
    };
    renderWithQuery(
      <MemoryRouter>
        <SkillsModal {...props} />
      </MemoryRouter>,
    );
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));
    await screen.findByTestId("pick-install-e-1");

    for (const id of ["e-2", "e-3"]) {
      expect(screen.getByTestId(`pick-taken-${id}`)).toHaveTextContent(
        word("skills.fromHub.taken"),
      );
      expect(screen.getByTestId(`pick-install-${id}`)).toBeDisabled();
    }
    for (const id of ["e-1", "e-4"]) {
      expect(screen.queryByTestId(`pick-taken-${id}`)).toBeNull();
      expect(screen.getByTestId(`pick-install-${id}`)).toBeEnabled();
    }
    // The mark and the button are one cluster that wraps under the text
    // (D13, the panel row's shape) — pinned by structure, happy-dom lays
    // nothing out.
    const cluster = screen.getByTestId("pick-actions-e-2");
    expect(cluster).toContainElement(screen.getByTestId("pick-taken-e-2"));
    expect(cluster).toContainElement(screen.getByTestId("pick-install-e-2"));
  });

  it("lists a fork under its root as one more thing to install", async () => {
    const hub = fakeHub([
      hubCard({
        forks: [
          hubCard({
            id: "e-fork",
            owner: "bob",
            forked_from: "e-1",
            missing_tools: [],
          }),
        ],
      }),
    ]);
    renderWithHub(hub);
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));

    expect(await screen.findByTestId("pick-e-fork")).toBeInTheDocument();
    expect(screen.queryByTestId("pick-missing-e-fork")).toBeNull();
  });
  it("Publish is a deliberate exit, so it asks about unsaved picks first (#779)", async () => {
    // Review round 1: Publish called the bare `onClose`, throwing away every
    // tri-state pick in silence while Escape politely asked.
    const props = renderModal();
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skill-author-skill-off"));
    const offered: string[] = [];
    const unsubscribe = subscribeAgentDraft("i1", (text) => offered.push(text));

    fireEvent.click(screen.getByTestId("skill-publish-my-skill"));

    // The sentence is in the box either way; the panel asks before it goes.
    expect(offered).toHaveLength(1);
    expect(await screen.findByTestId("dialog-action-keep")).toBeInTheDocument();
    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("dialog-action-discard"));
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
    unsubscribe();
  });

  it("offers Update and Reset only while the copy's upstream is live", async () => {
    // Review round 1: Reset on a copy whose original was unpublished or
    // deleted did nothing and then said "Updated to the shipped version".
    const skills: ItemSkillState[] = [
      {
        ...SKILLS[2],
        name: "gone",
        is_copy: true,
        upstream: "deleted",
        update_available: true,
      },
      { ...SKILLS[2], name: "hidden", is_copy: true, upstream: "unpublished" },
      {
        ...SKILLS[2],
        name: "fine",
        is_copy: true,
        upstream: "live",
        update_available: true,
      },
    ];
    renderModal({ client: fakeClient(skills) });

    await screen.findByTestId("skill-row-fine");
    expect(screen.getByTestId("skill-reset-fine")).toBeInTheDocument();
    expect(screen.getByTestId("skill-refresh-fine")).toBeInTheDocument();
    expect(screen.queryByTestId("skill-reset-gone")).toBeNull();
    expect(screen.queryByTestId("skill-refresh-gone")).toBeNull();
    expect(screen.queryByTestId("skill-reset-hidden")).toBeNull();
  });

  it("says on the row when a copy's skill hub original was unpublished or deleted", async () => {
    const skills: ItemSkillState[] = [
      { ...SKILLS[2], name: "gone", is_copy: true, upstream: "deleted" },
      { ...SKILLS[2], name: "hidden", is_copy: true, upstream: "unpublished" },
      { ...SKILLS[2], name: "fine", is_copy: true, upstream: "live" },
    ];
    renderModal({ client: fakeClient(skills) });

    expect(await screen.findByTestId("skill-upstream-gone")).toHaveTextContent(
      word("skillHub.origin.deleted"),
    );
    expect(screen.getByTestId("skill-upstream-hidden")).toHaveTextContent(
      word("skillHub.origin.unpublished"),
    );
    expect(screen.queryByTestId("skill-upstream-fine")).toBeNull();
  });
});
