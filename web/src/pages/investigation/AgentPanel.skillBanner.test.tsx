// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import type { ItemSkillState } from "../../api/types";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { translate } from "../../lib/i18n";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

// plan-skill-hub-release-notes D7 — the user on the first build: 「他要打開面板
// 才看得到 -> 不會有人看到」. The reminder is on the item, above the composer,
// for everyone who opens it; Update only for whoever may edit (D4).
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "alice",
  useCurrentUserState: () => ({ id: "alice", ready: true }),
}));

const word = (key: string, vars?: Record<string, string | number>) => translate("zh-TW", key as never, vars);

const BEHIND: ItemSkillState = {
  name: "log-digest",
  description: "",
  source: "workspace",
  default_on: true,
  is_copy: true,
  copy_of: "hub",
  hub_entry: "e1",
  update_available: true,
  upstream: "live",
  pref: "follow",
  effective: true,
  updates: [{ version: 2, kind: "publish", at: "2026-11-03T12:00:00Z", release_note: "Flags FATAL.", back_to: null }],
};

function stubAgent(): AgentState {
  return {
    investigationId: "it1",
    log: { entries: [], streaming: false } as unknown as AgentState["log"],
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function open(canUpdateSkills: boolean | undefined) {
  renderWithQuery(
    <MemoryRouter>
      <DialogProvider>
        <WorkspaceSlugProvider value="rca">
          <AgentPanel
            investigationId="it1"
            chatId="chat-1"
            agent={stubAgent()}
            picker={[]}
            suggestions={[]}
            attachedPreset=""
            onAttachPreset={() => {}}
            uploadDir="uploads"
            canUpdateSkills={canUpdateSkills}
          />
        </WorkspaceSlugProvider>
      </DialogProvider>
    </MemoryRouter>,
  );
}

const banner = () => screen.findByRole("region", { name: word("skills.banner.label") });
const updateButton = (el: HTMLElement) =>
  within(el).queryByRole("button", { name: new RegExp(`^${word("skills.updates.update")}$`) });

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
  vi.spyOn(api, "getItemSkills").mockResolvedValue([BEHIND] as never);
});
afterEach(cleanup);

describe("AgentPanel skill update banner", () => {
  it("is on the item without opening anything, and offers Update to an editor", async () => {
    open(true);
    const b = await banner();
    expect(b).toHaveTextContent(word("skills.banner.one", { name: "log-digest", version: 2 }));
    fireEvent.click(within(b).getByRole("button", { name: word("skills.updates.show") }));
    expect(updateButton(await banner())).toBeInTheDocument();
  });

  it("shows the notes but no Update to someone who may not edit", async () => {
    open(undefined);
    fireEvent.click(within(await banner()).getByRole("button", { name: word("skills.updates.show") }));
    const b = await banner();
    expect(b).toHaveTextContent("Flags FATAL.");
    expect(updateButton(b)).toBeNull();
  });
});
