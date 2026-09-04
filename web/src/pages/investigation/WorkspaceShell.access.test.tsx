// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

// This test is about ONE decision — whether the IDE column renders — so every
// heavy child (live chat SSE, presence, activity feed, the file service) is
// stubbed down to a marker. `ActivityBar`, the thing we assert on, is internal
// to WorkspaceShell and only mounts inside the `read_content` branch.
const chatReadOnly = vi.fn();
const chatProps = vi.fn();
vi.mock("../../components/ItemChatShell", () => ({
  ItemChatShell: (props: { readOnly?: boolean }) => {
    chatReadOnly(props.readOnly);
    chatProps(props);
    return <div data-testid="chat" />;
  },
}));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));

const isSuperuser = vi.fn(() => false);
vi.mock("../../hooks/useIsSuperuser", () => ({
  useIsSuperuser: () => isSuperuser(),
  useIsSuperuserState: () => ({ isSuperuser: isSuperuser(), ready: true }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "root",
  useCurrentUserState: () => ({ id: "root", ready: true }),
}));

const manifest = {
  slug: "rca",
  title: "RCA",
  icon: "flame",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: false },
  agent: { picker: [] },
  item: { noun: "Investigation", noun_plural: "Investigations" },
  layout: {
    breadcrumb: [],
    statusbar: [],
    list: [],
    default_tabs: [],
    primary_surface: "ide",
    chat_switcher: false,
  },
  labels: {},
  fields: [],
  field_styles: {},
  profiles: [],
  default_profile: "default",
  resource_route: "/rca-investigation",
} as unknown as AppManifest;

/** Someone else's private item — visible to an admin, owned by alice. */
const item = {
  resource_id: "INC-1",
  title: "Reflow drift",
  owner: "alice",
  created_by: "alice",
  permission: { visibility: "private" },
} as unknown as AppItem;

function open() {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={[]} />
    </MemoryRouter>,
  );
}

// #chat-private: a chat-first App swaps the members roster / role-ladder for the
// simple Share. Item owned by the current user ("root") so the shell isn't locked.
const chatManifest = {
  ...manifest,
  layout: { ...manifest.layout, primary_surface: "chat" },
} as unknown as AppManifest;
const myChat = {
  resource_id: "C-1",
  title: "My chat",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

describe("WorkspaceShell — chat-first sharing", () => {
  it("offers the simple Share (not the members roster) for a chat-first App", () => {
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={chatManifest} item={myChat} files={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: /Share/i })).toBeInTheDocument();
  });
});

beforeEach(() => isSuperuser.mockReturnValue(false));
afterEach(cleanup);

// The reported bug: an admin could see the work item in the list and open it,
// and then the workspace was simply not there — no activity bar, no file tree,
// no error. `read_content` was decided from the user id alone, so the admin fell
// into the `visibility === "private"` branch that the backend never applies to
// them.
describe("WorkspaceShell — who gets the IDE column", () => {
  it("renders the workspace for a superuser on someone else's private item", async () => {
    isSuperuser.mockReturnValue(true);
    open();
    expect(await screen.findByTitle("Search files")).toBeInTheDocument();
    // The other half of the symptom: the composer was read-only too.
    expect(chatReadOnly).toHaveBeenLastCalledWith(false);
  });

  it("still hides it from a plain non-owner with no read_content", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("page-item")).toBeInTheDocument());
    expect(screen.queryByTitle("Search files")).not.toBeInTheDocument();
  });

  // The destructure comment always promised to lock the panels the user lacks
  // the verb for "instead of a raw 403 from the file / chat sub-route" — but
  // only the file half was wired. The chat shell mounted regardless, so a
  // member without read_chat got the live chat chrome and a bare 403 stream.
  it("locks the chat pane for a member without read_chat instead of mounting the live chat", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("chat-locked")).toBeInTheDocument());
    expect(screen.queryByTestId("chat")).not.toBeInTheDocument();
  });

  it("mounts the live chat for a superuser on someone else's private item", async () => {
    isSuperuser.mockReturnValue(true);
    open();
    expect(await screen.findByTestId("chat")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-locked")).not.toBeInTheDocument();
  });

  // The middle tier: read_chat grants ENTRY, converse grants the composer.
  // Without this pin, `readOnly={!_canConverse}` could regress to a constant
  // and every test above would still pass (they only cover the two extremes).
  it("mounts the chat read-only for a viewer granted read_chat but not converse", async () => {
    const viewerItem = {
      ...item,
      permission: { visibility: "restricted", read_chat: ["user:root"] },
    } as unknown as AppItem;
    renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={manifest} item={viewerItem} files={[]} />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("chat")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-locked")).not.toBeInTheDocument();
    expect(chatReadOnly).toHaveBeenLastCalledWith(true);
  });
});

// The item's own FIELDS (env vars, tool/skill prefs, the attached preset) are
// stored by a PATCH the backend gates on `write_meta`. The shell handed the save
// callbacks down unconditionally, so a Participant got the Env button, typed
// their keys in, pressed Save — and the 403 was dropped on the floor with the
// panel closing as if it had worked. The affordance now follows the verb.
describe("WorkspaceShell — who may edit the item's fields", () => {
  /** A Participant on someone else's item: may talk to the agent, may not
   *  store a field on it. */
  const participantItem = {
    ...item,
    permission: {
      visibility: "restricted",
      read_meta: ["user:root"],
      read_chat: ["user:root"],
      read_content: ["user:root"],
      converse: ["user:root"],
    },
  } as unknown as AppItem;

  function openAs(withItem: AppItem) {
    return renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={manifest} item={withItem} files={[]} />
      </MemoryRouter>,
    );
  }

  it("withholds the env-var save from a Participant (no Env button, no silent 403)", async () => {
    openAs(participantItem);
    // Wait on the DENIED verb, not a granted one: every verb reads true until
    // identity resolves, so asserting too early passes for the wrong reason.
    await waitFor(() =>
      expect(chatProps).toHaveBeenLastCalledWith(
        expect.objectContaining({ onSaveEnvVars: undefined }),
      ),
    );
    // Still a full participant otherwise — this is a narrow gate, not a lockout.
    expect(chatReadOnly).toHaveBeenLastCalledWith(false);
  });

  it("hands the same Participant's saves to the owner", async () => {
    openAs({ ...participantItem, created_by: "root", owner: "root" } as unknown as AppItem);
    await waitFor(() => expect(screen.getByTestId("chat")).toBeInTheDocument());
    expect(chatProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ onSaveEnvVars: expect.any(Function) }),
    );
  });

  // The top bar's own two writers — the details gear and the inline domain-field
  // chips — are that same PATCH wearing different clothes.
  it("hides the details gear from a Participant and keeps it for the owner", async () => {
    openAs(participantItem);
    await waitFor(() => expect(screen.getByTestId("page-item")).toBeInTheDocument());
    expect(screen.queryByLabelText("Edit item details")).not.toBeInTheDocument();

    cleanup();
    openAs({ ...participantItem, created_by: "root", owner: "root" } as unknown as AppItem);
    expect(await screen.findByLabelText("Edit item details")).toBeInTheDocument();
  });

  // The env panel was the reported one, but the tool + skill pickers ride the
  // same PATCH and were offered on the same terms.
  it("withholds the tool and skill pref saves too — they are the same PATCH", async () => {
    openAs(participantItem);
    await waitFor(() =>
      expect(chatProps).toHaveBeenLastCalledWith(
        expect.objectContaining({ onSaveToolPrefs: undefined, onSaveSkillPrefs: undefined }),
      ),
    );
  });

  // The exception, pinned so it reads as a decision rather than an oversight:
  // the preset lives in the model dropdown alongside the VIEWER's own reasoning
  // / retrieval settings, which write_meta does not govern. Hiding it to protect
  // one field would take those away too, so it stays and a failed pick is caught
  // by the write-failure notice instead.
  it("keeps the model preset picker — it also holds settings that are the viewer's own", async () => {
    openAs(participantItem);
    await waitFor(() =>
      expect(chatProps).toHaveBeenLastCalledWith(
        expect.objectContaining({ onAttachPreset: expect.any(Function) }),
      ),
    );
  });
});


describe("WorkspaceShell — who may resize the environment", () => {
  // Round-12 R12-10. The panel honours `canEdit` and that IS tested; what was
  // not tested is that the right VALUE reaches it. Both links of the chain —
  // `canResize: _canManageAccess` here and `canEdit={environment.canResize}` in
  // AgentPanel — could be replaced with `true` and the whole 3205-test suite
  // stayed green. A permission enforced only by the component being handed the
  // right answer needs the handing-over asserted too.
  const sandboxManifest = {
    ...manifest,
    function: { ...manifest.function, sandbox: true },
  } as unknown as AppManifest;

  /** Someone else's item, shared with me: I may talk to the agent, I may not
   *  spend the owner's quota. */
  const participantItem = {
    ...item,
    permission: {
      visibility: "restricted",
      read_meta: ["user:root"],
      read_chat: ["user:root"],
      read_content: ["user:root"],
      converse: ["user:root"],
    },
  } as unknown as AppItem;

  function openAs(withItem: AppItem, m: AppManifest = sandboxManifest) {
    return renderWithQuery(
      <MemoryRouter>
        <WorkspaceShell manifest={m} item={withItem} files={[]} />
      </MemoryRouter>,
    );
  }

  it("withholds it from a Participant on someone else's item", async () => {
    openAs(participantItem);
    // Wait on the DENIED answer, exactly as the sibling suite does: every verb
    // reads true until identity resolves, so asserting too early passes for the
    // wrong reason. My first version of this test asserted on a stale
    // `chatProps` call left by an earlier test and failed for a third reason
    // again — the trap this whole round is about.
    await waitFor(() =>
      expect(chatProps).toHaveBeenLastCalledWith(
        expect.objectContaining({ environment: { canResize: false } }),
      ),
    );
    // Still a full participant otherwise — a narrow gate, not a lockout.
    expect(chatReadOnly).toHaveBeenLastCalledWith(false);
  });

  it("grants it to the owner", async () => {
    openAs({ ...participantItem, owner: "root", created_by: "root" } as unknown as AppItem);
    await waitFor(() =>
      expect(chatProps).toHaveBeenLastCalledWith(
        expect.objectContaining({ environment: { canResize: true } }),
      ),
    );
  });

  it("offers no environment at all for an App with no sandbox", async () => {
    openAs({ ...participantItem, owner: "root", created_by: "root" } as unknown as AppItem, manifest);
    await waitFor(() => expect(screen.getByTestId("chat")).toBeInTheDocument());
    expect(chatProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ environment: undefined }),
    );
  });
});
