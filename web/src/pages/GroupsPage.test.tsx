// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Group, GroupsApi } from "../api/groups";
import { mockGroupsApi } from "../api/groups";
import { QueryWrap } from "../test/queryWrapper";
import { GroupsPage } from "./GroupsPage";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

const me = vi.fn(() => "alice");
const superuser = vi.fn(() => false);
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: () => me() }));
vi.mock("../hooks/useIsSuperuser", () => ({ useIsSuperuser: () => superuser() }));
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [
    { id: "alice", name: "Alice", section: "", email: "", photo_url: null },
    { id: "bob", name: "Bob", section: "", email: "", photo_url: null },
    { id: "carol", name: "Carol", section: "", email: "", photo_url: null },
    { id: "dave", name: "Dave", section: "", email: "", photo_url: null },
  ],
  useUser: (id: string) => ({ id, name: id, section: "", email: "", photo_url: null }),
}));

const grp = (over: Partial<Group> = {}): Group => ({
  resource_id: "g1",
  name: "Engineering",
  description: "the eng team",
  members: ["bob"],
  owner: "alice",
  maintainers: [],
  ...over,
});

function client(over: Partial<GroupsApi> = {}): GroupsApi {
  return { ...mockGroupsApi, ...over };
}

afterEach(() => {
  cleanup();
  me.mockReturnValue("alice");
  superuser.mockReturnValue(false);
});

describe("GroupsPage", () => {
  it("shows the New group action to a superuser only", async () => {
    superuser.mockReturnValue(true);
    render(<GroupsPage client={client({ listGroups: async () => [] })} />);
    expect(await screen.findByTestId("groups-new")).toBeInTheDocument();
  });

  it("hides New group from a non-superuser owner", async () => {
    render(<GroupsPage client={client({ listGroups: async () => [grp()] })} />);
    await screen.findByText("Engineering");
    expect(screen.queryByTestId("groups-new")).not.toBeInTheDocument();
  });

  it("lists my groups with member count and my role", async () => {
    render(<GroupsPage client={client({ listGroups: async () => [grp()] })} />);
    expect(await screen.findByText("Engineering")).toBeInTheDocument();
    expect(screen.getByText(/1 member/)).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
  });

  it("lets the owner add a member", async () => {
    const addMembers = vi.fn(async () => {});
    render(<GroupsPage client={client({ listGroups: async () => [grp()], addMembers })} />);
    await userEvent.click(await screen.findByRole("button", { name: /Edit Engineering/ }));
    await userEvent.click(await screen.findByTestId("group-members-add"));
    await userEvent.click(within(await screen.findByTestId("group-members-picker")).getByText("Carol"));
    await waitFor(() => expect(addMembers).toHaveBeenCalledWith("g1", ["carol"]));
  });

  it("lets the owner delegate a maintainer, transfer, and delete", async () => {
    const addMaintainers = vi.fn(async () => {});
    const transferOwner = vi.fn(async () => grp({ owner: "dave" }));
    const deleteGroup = vi.fn(async () => {});
    render(
      <GroupsPage
        client={client({
          listGroups: async () => [grp()],
          addMaintainers,
          transferOwner,
          deleteGroup,
        })}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Edit Engineering/ }));
    // maintainer delegation
    await userEvent.click(await screen.findByTestId("group-maintainers-add"));
    await userEvent.click(
      within(await screen.findByTestId("group-maintainers-picker")).getByText("Dave"),
    );
    await waitFor(() => expect(addMaintainers).toHaveBeenCalledWith("g1", ["dave"]));
    // transfer + delete controls are present for the owner
    expect(screen.getByTestId("group-transfer")).toBeInTheDocument();
    expect(screen.getByTestId("group-delete")).toBeInTheDocument();
  });

  it("gives a maintainer the member editor but NOT maintainer/transfer/delete", async () => {
    me.mockReturnValue("dave");
    render(
      <GroupsPage
        client={client({ listGroups: async () => [grp({ maintainers: ["dave"] })] })}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Edit Engineering/ }));
    expect(await screen.findByTestId("group-members-add")).toBeInTheDocument();
    expect(screen.queryByTestId("group-maintainers-add")).not.toBeInTheDocument();
    expect(screen.queryByTestId("group-transfer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("group-delete")).not.toBeInTheDocument();
  });

  it("creates a group as a superuser, designating an owner", async () => {
    superuser.mockReturnValue(true);
    const createGroup = vi.fn(async () => grp());
    render(<GroupsPage client={client({ listGroups: async () => [], createGroup })} />);
    await userEvent.click(await screen.findByTestId("groups-new"));
    await userEvent.type(await screen.findByLabelText(/group name/i), "Design");
    await userEvent.click(await screen.findByTestId("group-owner-pick"));
    await userEvent.click(within(await screen.findByTestId("group-owner-picker")).getByText("Bob"));
    await userEvent.click(screen.getByRole("button", { name: /^Create$/ }));
    await waitFor(() =>
      expect(createGroup).toHaveBeenCalledWith({ name: "Design", description: "", owner: "bob" }),
    );
  });
});
