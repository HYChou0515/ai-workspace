// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import type { ItemSkillState } from "../api/types";
import { translate } from "../lib/i18n";
import { reminderOf } from "../lib/skillUpdateReminders";
import { renderWithQuery } from "../test/queryWrapper";
import { SkillsModal } from "./SkillsModal";

// plan-skill-hub-release-notes P5: a hub copy behind its entry says so in
// words, shows what the author wrote about each version since its own, and
// offers Update (to whoever may edit the item), Later and — from the second
// reminder on — Skip.
const who = vi.hoisted(() => ({ state: { id: "alice", ready: true } }));
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => who.state.id,
  useCurrentUserState: () => who.state,
}));

const word = (key: string, vars?: Record<string, string | number>) => translate("zh-TW", key as never, vars);
const T0 = Date.UTC(2026, 10, 4, 9);
const DAY = 24 * 3600 * 1000;

const BEHIND: ItemSkillState = {
  name: "triage",
  description: "Triage reflow defects.",
  source: "workspace",
  default_on: true,
  is_copy: true,
  copy_of: "hub",
  hub_entry: "e1",
  update_available: true,
  upstream: "live",
  pref: "follow",
  effective: true,
  updates: [
    { version: 3, kind: "publish", at: "2026-11-03T12:00:00Z", release_note: "Adds the **oven** check.", back_to: null },
    { version: 2, kind: "publish", at: "2026-11-01T12:00:00Z", release_note: "", back_to: null },
  ],
};

function mount(overrides: Partial<ComponentProps<typeof SkillsModal>> = {}) {
  const client = {
    getItemSkills: vi.fn(async () => [BEHIND]),
    refreshItemSkill: vi.fn(async () => ({ updated: ["SKILL.md"], skipped: [], removed: [] })),
  };
  renderWithQuery(
    <MemoryRouter>
      <SkillsModal
        slug="rca"
        itemId="i1"
        fileService={{ scopeId: "i1" } as unknown as FileService}
        onClose={vi.fn()}
        client={client}
        {...overrides}
      />
    </MemoryRouter>,
  );
  return client;
}

const row = () => screen.findByTestId("skill-row-triage");
const notes = () => screen.findByRole("region", { name: word("skills.updates.title", { name: "triage" }) });

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

describe("SkillsModal skill hub updates", () => {
  it("says there is a new version in words, and shows every version's note since this copy, newest first", async () => {
    mount();
    expect(await row()).toHaveTextContent(word("skills.updates.badge", { version: 3 }));
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    const region = await notes();
    const versions = within(region).getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(versions[0]).toContain("v3");
    expect(versions[1]).toContain("v2");
    expect(within(region).getByText("oven").tagName).toBe("STRONG"); // markdown, as the author wrote it
    expect(region).toHaveTextContent(word("skills.updates.noNote"));
  });

  it("first reminder: Update and Later, no Skip", async () => {
    mount({ canUpdate: true });
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    const region = await notes();
    expect(within(region).getByRole("button", { name: word("skills.updates.update") })).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: word("skills.updates.later") })).toBeInTheDocument();
    expect(within(region).queryByRole("button", { name: word("skills.updates.skip") })).toBeNull();
  });

  it("Later is recorded for this person and this version, and the row says until when", async () => {
    mount();
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await notes()).getByRole("button", { name: word("skills.updates.later") }));
    expect(reminderOf("alice", "e1", 3, T0 + DAY)).toMatchObject({ due: false, until: T0 + 7 * DAY });
    await waitFor(() => expect(screen.getByTestId("skill-row-triage")).toHaveTextContent(word("skills.updates.snoozed")));
  });

  it("once a Later has run out, the reminder offers Skip too", async () => {
    mount();
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await notes()).getByRole("button", { name: word("skills.updates.later") }));
    cleanup();
    vi.setSystemTime(T0 + 7 * DAY);
    mount();
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await notes()).getByRole("button", { name: word("skills.updates.skip") }));
    expect(reminderOf("alice", "e1", 3, T0 + 30 * DAY)).toMatchObject({ skipped: true });
  });

  it("Update is offered only to someone who may edit the item; the notes are shown to everyone", async () => {
    mount({ canUpdate: false });
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    const region = await notes();
    expect(within(region).queryByRole("button", { name: word("skills.updates.update") })).toBeNull();
    expect(region).toHaveTextContent("oven");
  });

  it("Update installs the latest version through the existing sync", async () => {
    const client = mount({ canUpdate: true });
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    fireEvent.click(within(await notes()).getByRole("button", { name: word("skills.updates.update") }));
    await waitFor(() => expect(client.refreshItemSkill).toHaveBeenCalledWith("rca", "i1", "triage", { force: false }));
  });

  it("judges nothing before it knows who is looking (#997)", async () => {
    who.state = { id: "default-user", ready: false };
    mount();
    expect(await row()).toHaveTextContent(word("skills.updates.badge", { version: 3 }));
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    expect(within(await notes()).queryByRole("button", { name: word("skills.updates.later") })).toBeNull();
  });

  // Review round 1 (#1004): D5 says a rollback below the installed version
  // READS "作者已把這個 skill 回到 vN" — the row's own line said "有新版本".
  it("a rollback says so on the row itself, not only inside the notes", async () => {
    const rolled: ItemSkillState = {
      ...BEHIND,
      updates: [{ version: 3, kind: "rollback", at: "2026-11-03T12:00:00Z", release_note: "", back_to: 1 }],
    };
    mount({ canUpdate: true, client: { getItemSkills: vi.fn(async () => [rolled]), refreshItemSkill: vi.fn() } });
    const line = await row();
    expect(line).toHaveTextContent(word("skills.updates.rolledBackBadge", { back: 1 }));
    expect(line).not.toHaveTextContent(word("skills.updates.badge", { version: 3 }));
  });

  // The same review: Update defaulted to SHOWN, so a caller that forgot the
  // prop offered everyone a button the route answers 403. It fails closed.
  it("offers no Update to a caller that does not say the viewer may edit", async () => {
    mount();
    fireEvent.click(within(await row()).getByRole("button", { name: word("skills.updates.show") }));
    expect(within(await notes()).queryByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) })).toBeNull();
  });
});
