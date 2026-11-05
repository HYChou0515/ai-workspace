// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ItemSkillState } from "../api/types";
import { translate } from "../lib/i18n";
import { reminderOf } from "../lib/skillUpdateReminders";
import { renderWithQuery } from "../test/queryWrapper";
import { SkillUpdateBanner } from "./SkillUpdateBanner";

// The user, on the first build (#1004): 「他要打開面板才看得到 -> 不會有人看到」.
// The mark sat on the skills button — folded into "⋯" at an ordinary width —
// and the notes were three clicks away. A banner at the top of the item says it
// on opening the item (Material 3 Banners: one at a time, at most two text
// buttons, dismissed by an action that addresses it, never a lone ×).
const who = vi.hoisted(() => ({ state: { id: "alice", ready: true } }));
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => who.state.id,
  useCurrentUserState: () => who.state,
}));

const word = (key: string, vars?: Record<string, string | number>) => translate("zh-TW", key as never, vars);
const T0 = Date.UTC(2026, 10, 4, 9);

const behind = (name: string, entry: string, version: number, note = "Adds the oven check."): ItemSkillState => ({
  name,
  description: "",
  source: "workspace",
  default_on: true,
  is_copy: true,
  copy_of: "hub",
  hub_entry: entry,
  update_available: true,
  upstream: "live",
  pref: "follow",
  effective: true,
  updates: [{ version, kind: "publish", at: "2026-11-03T12:00:00Z", release_note: note, back_to: null }],
});

function mount(skills: ItemSkillState[], canUpdate = true) {
  const client = {
    getItemSkills: vi.fn(async () => skills),
    refreshItemSkill: vi.fn(async () => ({ updated: ["SKILL.md"], skipped: [], removed: [] })),
  };
  renderWithQuery(
    <MemoryRouter>
      <SkillUpdateBanner slug="rca" itemId="i1" canUpdate={canUpdate} client={client} />
    </MemoryRouter>,
  );
  return client;
}

const banner = () => screen.findByRole("region", { name: word("skills.banner.label") });

beforeEach(() => {
  localStorage.clear();
  who.state = { id: "alice", ready: true };
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(T0);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("SkillUpdateBanner", () => {
  it("says which skill has a new version, with two text actions and no lone close", async () => {
    mount([behind("log-digest", "e1", 2)]);
    const b = await banner();
    expect(b).toHaveTextContent(word("skills.banner.one", { name: "log-digest", version: 2 }));
    const buttons = within(b).getAllByRole("button").map((x) => x.textContent);
    expect(buttons).toEqual([word("skills.updates.show"), word("skills.updates.later")]);
  });

  it("opens the notes in place, with Update for someone who may edit", async () => {
    mount([behind("log-digest", "e1", 2, "Now flags FATAL too.")]);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    const b = await banner();
    expect(b).toHaveTextContent("Now flags FATAL too.");
    expect(within(b).getByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) })).toBeInTheDocument();
  });

  // The live demo: opened, the banner said everything twice — its own line,
  // then the panel row's line again with a second Later. The notes open bare.
  it("opens the notes without repeating its own line or its Later", async () => {
    mount([behind("log-digest", "e1", 2, "Now flags FATAL too.")]);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    const b = await banner();
    expect(within(b).getAllByRole("button", { name: word("skills.updates.show") })).toHaveLength(1);
    expect(within(b).getAllByRole("button", { name: word("skills.updates.later") })).toHaveLength(1);
    expect(within(b).getAllByText(word("skills.banner.one", { name: "log-digest", version: 2 }))).toHaveLength(1);
  });

  it("offers no Update to someone who may not edit; the notes still open", async () => {
    mount([behind("log-digest", "e1", 2, "Now flags FATAL too.")], false);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    const b = await banner();
    expect(b).toHaveTextContent("Now flags FATAL too.");
    expect(within(b).queryByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) })).toBeNull();
  });

  it("Later puts off every skill it names, for this person, and the banner goes", async () => {
    mount([behind("log-digest", "e1", 2), behind("triage", "e2", 5)]);
    const b = await banner();
    expect(b).toHaveTextContent(word("skills.banner.many", { n: 2, names: "log-digest、triage" }));
    fireEvent.click(within(b).getByRole("button", { name: word("skills.updates.later") }));
    await waitFor(() => expect(screen.queryByRole("region", { name: word("skills.banner.label") })).toBeNull());
    expect(reminderOf("alice", "e1", 2, T0).due).toBe(false);
    expect(reminderOf("alice", "e2", 5, T0).due).toBe(false);
  });

  it("Update brings the latest version through the existing sync", async () => {
    const client = mount([behind("log-digest", "e1", 2)]);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) }));
    await waitFor(() => expect(client.refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "log-digest", { force: false }));
  });

  it("shows nothing while nothing is due, and nothing before it knows who is looking (#997)", async () => {
    mount([]);
    who.state = { id: "default-user", ready: false };
    cleanup();
    mount([behind("log-digest", "e1", 2)]);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("region", { name: word("skills.banner.label") })).toBeNull();
  });
});

// The user: 「如果有10個skill都有新版你怎麼顯示 — NN/g!」. NN/g, Push
// notifications: more than five pending ones are merged into one message.
// Accordions on desktop: many items are collapsed, the heading carries what
// decides whether to open it (critical content not hidden), with Expand all.
// Bulk actions: one action for the same change on many items — here Update
// all, confirmed first (the user's call: it rewrites copies everyone shares).
describe("SkillUpdateBanner with many skills", () => {
  const ten = Array.from({ length: 10 }, (_, i) =>
    behind(`skill-${i}`, `e${i}`, i + 2, `- First line of ${i}.\n- More about ${i}.`),
  );

  it("is one message naming the first two, and lists every skill collapsed with what decides opening it", async () => {
    mount(ten);
    const b = await banner();
    expect(b).toHaveTextContent(word("skills.banner.many", { n: 10, names: `skill-0、skill-1${word("skills.banner.andMore")}` }));
    fireEvent.click(within(b).getByRole("button", { name: word("skills.updates.show") }));
    const rows = within(await banner()).getAllByRole("listitem");
    expect(rows).toHaveLength(10);
    // Collapsed: name, version and the note's first line in the heading; the rest hidden.
    expect(rows[3]).toHaveTextContent("skill-3");
    expect(rows[3]).toHaveTextContent("v5");
    expect(rows[3]).toHaveTextContent("First line of 3.");
    expect(rows[3]).not.toHaveTextContent("More about 3.");
  });

  it("Expand all opens every row, Collapse all closes them", async () => {
    mount(ten);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.expandAll") }));
    expect(await banner()).toHaveTextContent("More about 9.");
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.collapseAll") }));
    expect(await banner()).not.toHaveTextContent("More about 9.");
  });

  it("Update all asks first, naming them, and updates every listed skill only on yes", async () => {
    const client = mount(ten.slice(0, 3));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.updateAll") }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("skill-0");
    expect(dialog).toHaveTextContent("skill-2");
    fireEvent.click(within(dialog).getByRole("button", { name: word("skills.banner.cancel") }));
    // Let the answer settle: an update started after Cancel would be async too.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await new Promise((r) => setTimeout(r, 30));
    expect(client.refreshItemSkill).not.toHaveBeenCalled();

    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.updateAll") }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("skills.banner.updateAll") }));
    await waitFor(() => expect(client.refreshItemSkill).toHaveBeenCalledTimes(3));
    expect(client.refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "skill-2", { force: false });
  });

  it("offers no Update all to someone who may not edit", async () => {
    mount(ten, false);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    expect(within(await banner()).queryByRole("button", { name: word("skills.banner.updateAll") })).toBeNull();
  });
});

// Review round 3 (#1004).
describe("SkillUpdateBanner says what happened", () => {
  it("names the files an update left alone because they were edited here", async () => {
    let behindNow = true;
    const client = {
      getItemSkills: vi.fn(async () => (behindNow ? [behind("log-digest", "e1", 2)] : [])),
      refreshItemSkill: vi.fn(async () => {
        behindNow = false;
        return { updated: [], skipped: ["SKILL.md"], removed: [] };
      }),
    };
    renderWithQuery(
      <MemoryRouter>
        <SkillUpdateBanner slug="rca" itemId="i1" canUpdate client={client} />
      </MemoryRouter>,
    );
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) }));
    // The region is mounted with the banner (so a reader hears what is put in it): wait for the words.
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("SKILL.md"));
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("log-digest");
    expect(status).toHaveTextContent("SKILL.md");
    expect(status).toHaveTextContent(word("skills.refreshKept"));
  });

  it("an update the server refuses says why, and a Later clears it", async () => {
    const client = mount([behind("log-digest", "e1", 2)]);
    client.refreshItemSkill.mockRejectedValueOnce(new Error("This copy can only be reset."));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("This copy can only be reset."));
    fireEvent.click(within(await banner()).getAllByRole("button", { name: word("skills.updates.later") })[0]);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("a rollback says so on the banner's line (D5)", async () => {
    mount([
      { ...behind("log-digest", "e1", 3), updates: [{ version: 3, kind: "rollback", at: "2026-11-03T12:00:00Z", release_note: "", back_to: 1 }] },
    ]);
    expect(await banner()).toHaveTextContent(word("skills.banner.rolledBack", { name: "log-digest", back: 1 }));
  });
});

// Review of the many-skill banner (#1004).
describe("SkillUpdateBanner while and after updating", () => {
  /** A client whose list drops each skill once it has been refreshed. */
  function live(skills: ItemSkillState[], refresh?: (name: string) => Promise<unknown>) {
    let left = [...skills];
    const client = {
      getItemSkills: vi.fn(async () => left),
      refreshItemSkill: vi.fn(async (_s: string, _i: string, name: string) => {
        if (refresh) await refresh(name);
        left = left.filter((s) => s.name !== name);
        return { updated: ["SKILL.md"], skipped: [], removed: [] };
      }),
    };
    renderWithQuery(
      <MemoryRouter>
        <SkillUpdateBanner slug="rca" itemId="i1" canUpdate client={client} />
      </MemoryRouter>,
    );
    return client;
  }
  const three = () => [behind("a", "ea", 2), behind("b", "eb", 2), behind("c", "ec", 2)];
  const exact = (key: string) => ({ name: new RegExp(`^${word(key)}$`) });

  it("Expand all follows the rows on screen after one of them is updated", async () => {
    live(three());
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.expandAll") }));
    fireEvent.click(within(await banner()).getAllByRole("button", exact("skills.updates.update"))[0]);
    await waitFor(() => expect(within(screen.getByRole("region", { name: word("skills.banner.label") })).getAllByRole("listitem")).toHaveLength(2));
    // Both remaining rows are open, so the one bulk control offers to close them.
    expect(within(await banner()).getByRole("button", { name: word("skills.banner.collapseAll") })).toBeInTheDocument();
  });

  it("while updating, the controls are off and it says how far it got; then what was updated", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const client = live(three(), () => gate);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.updateAll") }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("skills.banner.updateAll") }));
    const b = await banner();
    await waitFor(() => expect(b).toHaveTextContent(word("skills.banner.updating", { done: 0, n: 3 })));
    for (const btn of within(b).getAllByRole("button")) expect(btn).toBeDisabled();
    fireEvent.click(within(b).getAllByRole("button", exact("skills.updates.update"))[0]);
    release();
    await waitFor(() => expect(client.refreshItemSkill).toHaveBeenCalledTimes(3));
    expect(await banner()).toHaveTextContent(word("skills.banner.updated", { names: "a、b、c" }));
  });

  it("what is left to say after the last update can be closed", async () => {
    let left = [behind("a", "ea", 2)];
    const client = {
      getItemSkills: vi.fn(async () => left),
      refreshItemSkill: vi.fn(async () => {
        left = [];
        return { updated: [], skipped: ["SKILL.md"], removed: [] };
      }),
    };
    renderWithQuery(
      <MemoryRouter>
        <SkillUpdateBanner slug="rca" itemId="i1" canUpdate client={client} />
      </MemoryRouter>,
    );
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", exact("skills.updates.update")));
    await screen.findByText(new RegExp("SKILL.md"));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.dismiss") }));
    await waitFor(() => expect(screen.queryByRole("region", { name: word("skills.banner.label") })).toBeNull());
  });

  it("one refusal among several is named; the others still update", async () => {
    const client = live(three(), async (name) => {
      if (name === "b") throw new Error("This copy can only be reset.");
    });
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.updateAll") }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("skills.banner.updateAll") }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        word("skills.banner.failedLine", { name: "b", error: "This copy can only be reset." }),
      ),
    );
    expect(client.refreshItemSkill).toHaveBeenCalledTimes(3);
  });

  it("Update all leaves out a copy that cannot be changed", async () => {
    const client = live([behind("a", "ea", 2), { ...behind("b", "eb", 2), readonly: true }]);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.banner.updateAll") }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(word("skills.banner.confirmTitleOne", { name: "a" }));
    fireEvent.click(within(dialog).getByRole("button", { name: word("skills.banner.updateAll") }));
    await waitFor(() => expect(client.refreshItemSkill).toHaveBeenCalledTimes(1));
    expect(client.refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "a", { force: false });
  });

  it("a row heading carries the note's words, not its markdown", async () => {
    mount([
      behind("a", "ea", 2, "## Fixes\n\n- more"),
      behind("b", "eb", 2, "Adds `read_file` and __init__ support, see [docs](https://x)."),
    ]);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    const rows = within(await banner()).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent(/a\s*v2\s*Fixes/);
    expect(rows[0]).not.toHaveTextContent("##");
    expect(rows[1]).toHaveTextContent("Adds read_file and __init__ support, see docs.");
  });
});
