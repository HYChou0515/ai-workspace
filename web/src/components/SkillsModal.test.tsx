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
import type { SkillHubBrowseQuery, SkillHubCard } from "../api/skillHub";
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
    refreshItemSkill: vi.fn(async () => ({ updated: [], skipped: [], removed: [] })),
  };
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

/** One action in a row's ⋯ menu (plan-skill-hub-ux-redo D9), that row's
 * menu opened first — or `null` when the row has no such action. Opened per
 * row: opening a menu moves focus out of any other, which closes it, so an
 * absence asserted against a closed menu would hold vacuously. */
async function action(name: string, kind: string): Promise<HTMLElement | null> {
  await screen.findByTestId(`skill-row-${name}`);
  const trigger = screen.queryByTestId(`skill-more-${name}`);
  if (trigger && trigger.getAttribute("aria-expanded") !== "true") fireEvent.click(trigger);
  return screen.queryByTestId(`skill-${kind}-${name}`);
}
async function mustAction(name: string, kind: string): Promise<HTMLElement> {
  const el = await action(name, kind);
  if (!el) throw new Error(`no ${kind} in ${name}'s ⋯ menu`);
  return el;
}

describe("SkillsModal (#380)", () => {
  it("lists skills across all sources with a source badge", async () => {
    renderModal();
    expect(await screen.findByTestId("skill-author-skill-follow")).toBeInTheDocument();
    expect(screen.getByTestId("skill-designed-pptx-off")).toBeInTheDocument();
    expect(screen.getByTestId("skill-my-skill-follow")).toBeInTheDocument();
    // In words, never the internal `workspace` / `shared` (D9).
    expect(screen.getByTestId("skill-source-my-skill")).toHaveTextContent(word("skills.source.workspace"));
    expect(screen.getByTestId("skill-source-author-skill")).toHaveTextContent(word("skills.source.shared"));
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
    expect(controls.length).toBe(5); // apply, three toggles, ⋯ — the rest is in ⋯ (D9)
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
    fireEvent.click((await mustAction("my-skill", "download")));
    await waitFor(() => expect(f.prepareDirDownload).toHaveBeenCalledWith(".skill/my-skill"));
    expect(f.dirDownloadUrl).toHaveBeenCalledWith("d1", ".skill/my-skill");
    expect(click).toHaveBeenCalled();
  });

  it("offers download only on workspace skills", async () => {
    renderModal();
    expect((await mustAction("my-skill", "download"))).toBeInTheDocument();
    expect((await action("author-skill", "download"))).toBeNull();
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
    expect((await mustAction("triage", "download"))).toBeInTheDocument();
  });

  it("says the copy is editable here, so its source badge is not the whole story", async () => {
    renderModal({ client: fakeClient(COPIED) as never });
    expect(await screen.findByTestId("skill-copy-triage")).toBeInTheDocument();
  });
});

// docs/plan-ai-reads-docs.md P1: a readonly skill's copy follows what the platform
// ships and nobody edits it, so "editable here", Update and Reset are all false
// promises on its row — it says what it is instead.
describe("SkillsModal — a readonly skill", () => {
  const READONLY: ItemSkillState[] = [
    {
      name: "system-help",
      description: "the system's docs",
      source: "shared",
      default_on: true,
      is_copy: true,
      copy_of: "shared",
      update_available: true,
      readonly: true,
      pref: "follow",
      effective: true,
    },
  ];

  // No pill of its own: like a shared skill that is never copied (chart,
  // grill-me), "no `editable here`" already says it cannot be changed here.
  it("reads like any skill you cannot edit here: no pill, no update or reset", async () => {
    renderModal({ client: fakeClient(READONLY) as never });
    expect(await screen.findByText("system-help")).toBeTruthy();
    expect(screen.queryByTestId("skill-readonly-system-help")).toBeNull();
    // its files ARE in the workspace (the copy), but they are the platform's,
    // not the person's to take away -- no download either, like chart
    expect((await action("system-help", "download"))).toBeNull();
    expect(screen.queryByTestId("skill-copy-system-help")).toBeNull();
    expect(screen.queryByTestId("skill-update-system-help")).toBeNull();
    expect((await action("system-help", "refresh"))).toBeNull();
    expect((await action("system-help", "reset"))).toBeNull();
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

  const NO_UPDATE: ItemSkillState[] = [{ ...COPIED[0], update_available: false }];

  it("pulls the shipped version and reports what it left alone", async () => {
    const refreshItemSkill = vi.fn(async () => ({
      updated: ["scripts/x.py"],
      skipped: ["scripts/tuned.py"],
      removed: [],
    }));
    renderModal({
      client: { ...fakeClient(COPIED), refreshItemSkill } as never,
    });

    fireEvent.click((await mustAction("triage", "refresh")));

    await waitFor(() =>
      expect(refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "triage", { force: false }),
    );
    // The files it did NOT touch are the ones the user needs told about.
    expect(await screen.findByText(/scripts\/tuned\.py/)).toBeInTheDocument();
  });

  it("says why a refresh was refused, in the server's own sentence (review round 1)", async () => {
    const { HttpError } = await import("../api/http");
    const sentence =
      "this copy of 'triage' no longer records which version it came from — reset it instead";
    const refreshItemSkill = vi.fn(async () => {
      throw new HttpError(409, sentence);
    });
    renderModal({ client: { ...fakeClient(COPIED), refreshItemSkill } as never });

    fireEvent.click((await mustAction("triage", "refresh")));

    expect(await screen.findByRole("alert")).toHaveTextContent(sentence);
  });

  it("offers reset-to-factory even when there is nothing new upstream", async () => {
    const refreshItemSkill = vi.fn(async () => ({ updated: [], skipped: [], removed: [] }));
    renderModal({ client: { ...fakeClient(NO_UPDATE), refreshItemSkill } as never });

    fireEvent.click((await mustAction("triage", "reset")));

    // The escape hatch for edits the per-file update deliberately refuses to
    // touch: without it, one bad edit by the AI has no way back.
    await waitFor(() =>
      expect(refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "triage", { force: true }),
    );
  });

  it("hides the update control when upstream has not moved", async () => {
    renderModal({ client: fakeClient(NO_UPDATE) as never });
    await screen.findByTestId("skill-row-triage");
    // A button whose only honest outcome is "nothing changed" reads as broken.
    expect((await action("triage", "refresh"))).toBeNull();
    expect((await mustAction("triage", "reset"))).toBeInTheDocument();
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

  it("says 「skill 已變更」 and offers 〔同步〕 on a skill hub copy, and keeps the package's words (G21)", async () => {
    const skills: ItemSkillState[] = [
      { ...COPIED[0], copy_of: "profile" },
      { ...COPIED[0], name: "from-hub", source: "workspace", upstream: "live", copy_of: "hub" },
    ];
    renderModal({ client: fakeClient(skills) as never });
    await screen.findByTestId("skill-row-from-hub");

    // The state in words with its action beside it (D9): 「skill 已變更 ・ 同步」.
    expect(screen.getByTestId("skill-update-from-hub")).toHaveTextContent("skill 已變更");
    expect(screen.getByTestId("skill-update-go-from-hub")).toHaveTextContent("同步");
    expect(screen.getByTestId("skill-update-triage")).toHaveTextContent("有新版");
    expect(screen.getByTestId("skill-update-go-triage")).toHaveTextContent(word("skills.refresh.short"));
  });

  it("names the skill hub in full, never as just 'hub' (G22)", () => {
    for (const key of ["skills.reset.hub", "skills.refreshDone.hub"] as const) {
      for (const locale of ["zh-TW", "en"] as const) {
        const text = translate(locale, key);
        expect(text, `${locale} ${key}`).toMatch(/skill hub/i);
        expect(text.replace(/skill hub/gi, ""), `${locale} ${key}`).not.toMatch(/\bhub\b/i);
      }
    }
    expect(translate("en", "skills.refresh.hub")).toBe("Sync");
    expect(translate("en", "skills.updateAvailable.hub")).toBe("Skill changed");
  });

  it("words Update / Reset and the note by where the copy came from — the package or the hub (D4)", async () => {
    // The listing says what a copy is OF (`copy_of`). `source` alone cannot:
    // a copy of a package skill this App does not declare lists as
    // `workspace` + `is_copy` exactly like a hub copy (review round 1 of
    // #826), and its Reset must still say the package's words.
    const skills: ItemSkillState[] = [
      { ...COPIED[0], copy_of: "profile" },
      {
        ...COPIED[0],
        name: "from-hub",
        source: "workspace",
        upstream: "live",
        copy_of: "hub",
      },
      {
        ...COPIED[0],
        name: "undeclared",
        source: "workspace",
        upstream: "live",
        copy_of: "shared",
      },
      // an older API pod mid-rollout sends no `copy_of`: the package's words
      { ...COPIED[0], name: "older-api", source: "workspace", upstream: "live" },
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

    expect((await mustAction("triage", "refresh"))).toHaveAccessibleName(word("skills.refresh"));
    expect((await mustAction("triage", "reset"))).toHaveAccessibleName(word("skills.reset"));
    expect((await mustAction("from-hub", "refresh"))).toHaveAccessibleName(word("skills.refresh.hub"));
    expect((await mustAction("from-hub", "reset"))).toHaveAccessibleName(word("skills.reset.hub"));
    expect((await mustAction("undeclared", "reset"))).toHaveAccessibleName(word("skills.reset"));
    expect((await mustAction("older-api", "reset"))).toHaveAccessibleName(word("skills.reset"));

    fireEvent.click((await mustAction("from-hub", "refresh")));
    expect(await screen.findByTestId("skills-refresh-note")).toHaveTextContent(
      word("skills.refreshDone.hub"),
    );
    fireEvent.click((await mustAction("triage", "refresh")));
    await waitFor(() =>
      expect(screen.getByTestId("skills-refresh-note")).toHaveTextContent(
        word("skills.refreshDone"),
      ),
    );
  });

  it("keeps a copy's other actions in its ⋯ menu, each in words, Apply and the state always in place (D9)", async () => {
    // The audit: a copy's row carried 1–5 icon-only buttons that pushed the
    // state badges under them and never lined up. The ⋯ menu holds the
    // rest, labelled in words; the trigger says whose actions they are.
    const skills: ItemSkillState[] = [
      { ...COPIED[0], name: "from-hub", source: "workspace", upstream: "live", copy_of: "hub" },
    ];
    renderModal({ client: fakeClient(skills) as never });
    const more = await screen.findByTestId("skill-more-from-hub");
    expect(more).toHaveAccessibleName(word("skills.more", { name: "from-hub" }));
    fireEvent.click(more);
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      word("skills.refresh.hub"),
      word("skills.reset.hub"),
      word("skills.download"),
      word("skills.publish"),
    ]);
  });

  it("a status that needs doing carries its action: 「有新版 ・ 更新」 updates (D9)", async () => {
    const refreshItemSkill = vi.fn(async () => ({ updated: ["SKILL.md"], skipped: [], removed: [] }));
    renderModal({ client: { ...fakeClient(COPIED), refreshItemSkill } as never });
    fireEvent.click(await screen.findByTestId("skill-update-go-triage"));
    await waitFor(() =>
      expect(refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "triage", { force: false }),
    );
  });

  it("offers no refresh for a skill that was written here", async () => {
    renderModal();
    await screen.findByTestId("skill-row-my-skill");
    expect((await action("my-skill", "refresh"))).toBeNull();
  });

  // #779: a long list of tri-states — re-picking through it is the cost, and
  // nothing is written until Save.
  it("asks before dropping unsaved skill picks, and keeps them", async () => {
    const props = renderModal();
    fireEvent.click(await screen.findByTestId("skill-author-skill-off"));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByTestId("skill-author-skill-off")).toHaveAttribute("aria-pressed", "true");
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

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

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
  installs: 0,
  uses: 0,
  fork_count: 0,
  origin: null,
  updated_at: null,
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
    browse: vi.fn(async (_query: SkillHubBrowseQuery) => ({
      entries: rows,
      total: rows.length,
      counted_since: "",
    })),
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
    expect((await action("author-skill", "publish"))).toBeNull();
    fireEvent.click((await mustAction("my-skill", "publish")));

    expect(offered).toEqual([word("skills.publishSentence", { name: "my-skill" })]);
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
      // Forks beside originals: the picker offers everything installable,
      // not the hub page's originals-only browse (review round 1).
      expect(hub.browse).toHaveBeenCalledWith(
        expect.objectContaining({ q: "", app: "rca", forks: true }),
      ),
    );
    // A page of the panel, not a second modal over it (D9).
    expect(screen.queryByTestId("skill-row-my-skill")).toBeNull();
    expect(document.querySelectorAll("[role=dialog]")).toHaveLength(1);
    expect(await screen.findByTestId("pick-missing-e-1")).toHaveTextContent(
      word("skills.fromHub.missing", { tools: "query_entity" }),
    );

    fireEvent.click(screen.getByTestId("pick-install-e-1"));

    await waitFor(() => expect(hub.install).toHaveBeenCalledWith("rca", "i1", "e-1"));
    await waitFor(() => expect(picker).not.toBeInTheDocument());
    expect(screen.getByTestId("skills-refresh-note")).toHaveTextContent(
      word("skills.fromHub.installed", { name: "triage-reflow" }),
    );
  });

  it("an install from the picker refreshes what the skill page says about installs (review round 1)", async () => {
    const qc = makeQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const hub = fakeHub();
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
      qc,
    );
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));
    fireEvent.click(await screen.findByTestId("pick-install-e-1"));

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["skillHub", "installs"] }));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["skillHub", "targets"] });
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
      word("skillHub.refused.folder_in_the_way.theirs", { owner: "alice", name: "triage-reflow" }),
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
    // The same four kinds the backend parity test walks
    // (`tests/api/test_skill_hub_panel.py`): a hand-written skill, a copy of
    // a package skill, a hub copy, a package skill with no folder here.
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
      {
        name: "from-hub",
        description: "installed from the hub",
        source: "workspace",
        default_on: true,
        is_copy: true,
        upstream: "live",
        pref: "follow",
        effective: true,
      },
    ];
    const hub = fakeHub([
      hubCard({ id: "e-1", name: "triage-reflow" }),
      hubCard({ id: "e-2", name: "my-skill", missing_tools: [] }),
      hubCard({ id: "e-3", name: "designed-pptx-copy", missing_tools: [] }),
      hubCard({ id: "e-4", name: "author-skill", missing_tools: [] }),
      hubCard({ id: "e-5", name: "from-hub", missing_tools: [] }),
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

    for (const id of ["e-2", "e-3", "e-5"]) {
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

  it("a fork among the rows says what it was forked from (plan-skill-hub-ux-redo D2)", async () => {
    const hub = fakeHub([
      hubCard({}),
      hubCard({
        id: "e-fork",
        owner: "bob",
        forked_from: "e-1",
        origin: { owner: "alice", name: "triage-reflow" },
        missing_tools: [],
      }),
    ]);
    renderWithHub(hub);
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));

    const fork = await screen.findByTestId("pick-e-fork");
    expect(fork).toHaveTextContent(word("skillHub.forkOf.named", { owner: "alice", name: "triage-reflow" }));
    expect(screen.queryByTestId("pick-missing-e-fork")).toBeNull();
  });

  it("goes back to the list from the picker without installing", async () => {
    const hub = fakeHub();
    renderWithHub(hub);
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skills-from-hub"));
    fireEvent.click(await screen.findByTestId("skill-hub-picker-back"));
    expect(await screen.findByTestId("skill-row-my-skill")).toBeInTheDocument();
    expect(hub.install).not.toHaveBeenCalled();
  });

  it("Publish is a deliberate exit, so it asks about unsaved picks first (#779)", async () => {
    // Review round 1: Publish called the bare `onClose`, throwing away every
    // tri-state pick in silence while Escape politely asked.
    const props = renderModal();
    await screen.findByTestId("skill-row-my-skill");
    fireEvent.click(screen.getByTestId("skill-author-skill-off"));
    const offered: string[] = [];
    const unsubscribe = subscribeAgentDraft("i1", (text) => offered.push(text));

    fireEvent.click((await mustAction("my-skill", "publish")));

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
      { ...SKILLS[2], name: "gone", is_copy: true, upstream: "deleted", update_available: true },
      { ...SKILLS[2], name: "hidden", is_copy: true, upstream: "unpublished" },
      { ...SKILLS[2], name: "fine", is_copy: true, upstream: "live", update_available: true },
    ];
    renderModal({ client: fakeClient(skills) });

    await screen.findByTestId("skill-row-fine");
    expect((await mustAction("fine", "reset"))).toBeInTheDocument();
    expect((await mustAction("fine", "refresh"))).toBeInTheDocument();
    expect((await action("gone", "reset"))).toBeNull();
    expect((await action("gone", "refresh"))).toBeNull();
    expect((await action("hidden", "reset"))).toBeNull();
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
