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
  updated_at: Date.parse("2026-08-20T00:00:00Z"),
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
    // Scoped to the row: "Owner" is now also a column header, and an unscoped
    // getByText matches both.
    expect(within(screen.getByTestId("group-row-g1")).getByText("Owner")).toBeInTheDocument();
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

/**
 * The overview was an unordered `<ul>`: fine for the two or three groups you
 * belong to, useless once an admin is looking at the org's. A table gives the
 * columns something to be sorted BY, and the search box is what makes a long
 * list navigable at all.
 */
describe("GroupsPage as a table", () => {
  const three = [
    grp({ resource_id: "g1", name: "Reflow", description: "solder line", owner: "alice", members: ["bob", "carol"] }),
    grp({ resource_id: "g2", name: "applications", description: "field support", owner: "bob", members: ["carol"] }),
    grp({ resource_id: "g3", name: "Manufacturing", description: "assembly", owner: "carol", members: [] }),
  ];
  const rows = () =>
    screen.queryAllByTestId(/^group-row-/).map((el) => el.getAttribute("data-group-name"));
  const renderThree = () => render(<GroupsPage client={client({ listGroups: async () => three })} />);

  it("renders the groups as a table", async () => {
    renderThree();
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(rows()).toHaveLength(3);
  });

  it("sorts by name by default, case-insensitively", async () => {
    renderThree();
    await screen.findByRole("table");
    expect(rows()).toEqual(["applications", "Manufacturing", "Reflow"]);
  });

  it("reverses that column when its header is pressed again", async () => {
    renderThree();
    await screen.findByRole("table");

    await userEvent.click(screen.getByTestId("groups-sort-name"));
    expect(rows()).toEqual(["Reflow", "Manufacturing", "applications"]);
  });

  it("sorts by member count as a number, not as text", async () => {
    // "10" < "2" lexically, which is exactly the bug a count column invites.
    render(
      <GroupsPage
        client={client({
          listGroups: async () => [
            grp({ resource_id: "a", name: "Ten", members: Array.from({ length: 10 }, (_, i) => `u${i}`) }),
            grp({ resource_id: "b", name: "Two", members: ["x", "y"] }),
          ],
        })}
      />,
    );
    await screen.findByRole("table");

    await userEvent.click(screen.getByTestId("groups-sort-members"));
    expect(rows()).toEqual(["Two", "Ten"]);
  });

  it("marks the sorted column for assistive tech", async () => {
    renderThree();
    await screen.findByRole("table");
    const header = () => screen.getByRole("columnheader", { name: /Name/ });
    expect(header()).toHaveAttribute("aria-sort", "ascending");

    await userEvent.click(screen.getByTestId("groups-sort-name"));
    expect(header()).toHaveAttribute("aria-sort", "descending");
  });

  it("filters by name as you type", async () => {
    renderThree();
    await screen.findByRole("table");

    await userEvent.type(screen.getByTestId("groups-search"), "refl");
    expect(rows()).toEqual(["Reflow"]);
  });

  it("filters by description and by owner too", async () => {
    renderThree();
    await screen.findByRole("table");

    await userEvent.type(screen.getByTestId("groups-search"), "field");
    expect(rows()).toEqual(["applications"]);

    await userEvent.clear(screen.getByTestId("groups-search"));
    await userEvent.type(screen.getByTestId("groups-search"), "carol");
    expect(rows()).toEqual(["Manufacturing"]);
  });

  it("says nothing matched rather than showing an empty table", async () => {
    renderThree();
    await screen.findByRole("table");

    await userEvent.type(screen.getByTestId("groups-search"), "zzz");
    expect(rows()).toEqual([]);
    expect(screen.getByText(/no groups match/i)).toBeInTheDocument();
  });

  it("still opens the editor from a row", async () => {
    renderThree();
    await screen.findByRole("table");

    await userEvent.click(screen.getByRole("button", { name: /Edit Reflow/i }));
    expect(await screen.findByTestId("group-members-add")).toBeInTheDocument();
  });
});

/**
 * The name is the only handle anyone has on a group — it is what the share
 * picker lists and searches — and it was the one field with no way to correct
 * it. Owner-or-admin, mirroring `_require_owner` on the server; a maintainer
 * manages MEMBERS, so the name others find the group by is not theirs to change.
 */
describe("GroupsPage renaming", () => {
  const renameFlow = async (name: string) => {
    await userEvent.click(await screen.findByRole("button", { name: /Rename Engineering/i }));
    const box = screen.getByRole("textbox", { name: /Group name/i });
    await userEvent.clear(box);
    await userEvent.type(box, name);
    await userEvent.keyboard("{Enter}");
  };

  it("lets the owner rename from the row", async () => {
    const renameGroup = vi.fn(async () => grp({ name: "Reflow" }));
    render(<GroupsPage client={client({ listGroups: async () => [grp()], renameGroup })} />);

    await renameFlow("Reflow");
    await waitFor(() => expect(renameGroup).toHaveBeenCalledWith("g1", "Reflow"));
  });

  it("lets an admin rename a group they do not own", async () => {
    me.mockReturnValue("zoe");
    superuser.mockReturnValue(true);
    const renameGroup = vi.fn(async () => grp({ name: "Reflow" }));
    render(<GroupsPage client={client({ listGroups: async () => [grp()], renameGroup })} />);

    await renameFlow("Reflow");
    await waitFor(() => expect(renameGroup).toHaveBeenCalledWith("g1", "Reflow"));
  });

  it("offers it to nobody else — not even a maintainer", async () => {
    me.mockReturnValue("dave");
    render(
      <GroupsPage client={client({ listGroups: async () => [grp({ maintainers: ["dave"] })] })} />,
    );
    await screen.findByText("Engineering");

    // The maintainer still manages members, so this is not "no access" — it is
    // this one action being out of reach.
    expect(screen.getByRole("button", { name: /Edit Engineering/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Rename Engineering/i })).not.toBeInTheDocument();
  });

  it("does not call the API when the name comes back unchanged", async () => {
    const renameGroup = vi.fn(async () => grp());
    render(<GroupsPage client={client({ listGroups: async () => [grp()], renameGroup })} />);

    await userEvent.click(await screen.findByRole("button", { name: /Rename Engineering/i }));
    await userEvent.keyboard("{Enter}");
    expect(renameGroup).not.toHaveBeenCalled();
  });

  it("refuses to send a blank name", async () => {
    // The server rejects it with a 422; not sending it at all keeps the row from
    // flickering through an error for something we can see is empty.
    const renameGroup = vi.fn(async () => grp());
    render(<GroupsPage client={client({ listGroups: async () => [grp()], renameGroup })} />);

    await userEvent.click(await screen.findByRole("button", { name: /Rename Engineering/i }));
    await userEvent.clear(screen.getByRole("textbox", { name: /Group name/i }));
    await userEvent.keyboard("{Enter}");
    expect(renameGroup).not.toHaveBeenCalled();
  });

  it("abandons the edit on Escape", async () => {
    const renameGroup = vi.fn(async () => grp());
    render(<GroupsPage client={client({ listGroups: async () => [grp()], renameGroup })} />);

    await userEvent.click(await screen.findByRole("button", { name: /Rename Engineering/i }));
    await userEvent.type(screen.getByRole("textbox", { name: /Group name/i }), "Whatever");
    await userEvent.keyboard("{Escape}");

    expect(renameGroup).not.toHaveBeenCalled();
    expect(screen.getByText("Engineering")).toBeInTheDocument();
  });
});

/**
 * "When did this last change?" is the question you ask of a list of groups you
 * did not create — and the answer was nowhere on the page.
 */
describe("GroupsPage last-updated column", () => {
  const rows = () =>
    screen.queryAllByTestId(/^group-row-/).map((el) => el.getAttribute("data-group-name"));

  const dated = [
    grp({ resource_id: "g1", name: "Older", updated_at: Date.parse("2026-08-01T00:00:00Z") }),
    grp({ resource_id: "g2", name: "Newest", updated_at: Date.parse("2026-08-25T00:00:00Z") }),
    grp({ resource_id: "g3", name: "Middle", updated_at: Date.parse("2026-08-10T00:00:00Z") }),
  ];
  const renderDated = () => render(<GroupsPage client={client({ listGroups: async () => dated })} />);

  it("shows when each group last changed", async () => {
    renderDated();
    await screen.findByRole("table");

    expect(screen.getByRole("columnheader", { name: /Last updated/i })).toBeInTheDocument();
    // A relative time, not a raw epoch number — "1755000000000" tells nobody
    // anything.
    const cell = within(screen.getByTestId("group-row-g2")).getByTestId("group-updated-g2");
    expect(cell.textContent).not.toMatch(/^\d{10,}$/);
    expect(cell.textContent?.trim()).toBeTruthy();
  });

  it("carries the exact timestamp as a tooltip", async () => {
    // The cell drops precision deliberately; hovering has to give it back, or
    // the column can tell you something is old but never when.
    renderDated();
    await screen.findByRole("table");

    const cell = screen.getByTestId("group-updated-g2");
    const title = cell.getAttribute("title") ?? "";
    expect(title).toBe(new Date(Date.parse("2026-08-25T00:00:00Z")).toLocaleString());
    // …and it is not just the same text as the cell.
    expect(title).not.toBe(cell.textContent?.trim());
  });

  it("sorts by it, newest first on the first press", async () => {
    // Recency is the useful direction, so it leads rather than making everyone
    // click twice.
    renderDated();
    await screen.findByRole("table");

    await userEvent.click(screen.getByTestId("groups-sort-updated"));
    expect(rows()).toEqual(["Newest", "Middle", "Older"]);

    await userEvent.click(screen.getByTestId("groups-sort-updated"));
    expect(rows()).toEqual(["Older", "Middle", "Newest"]);
  });

  it("compares it as a number", async () => {
    // The value is epoch ms; comparing it as text would order by first digit.
    render(
      <GroupsPage
        client={client({
          listGroups: async () => [
            grp({ resource_id: "a", name: "Nine", updated_at: 900_000_000_000 }),
            grp({ resource_id: "b", name: "Eleven", updated_at: 1_100_000_000_000 }),
          ],
        })}
      />,
    );
    await screen.findByRole("table");

    await userEvent.click(screen.getByTestId("groups-sort-updated"));
    expect(rows()).toEqual(["Eleven", "Nine"]);
  });
});

describe("New group — leaving with something typed (#779)", () => {
  const openIt = async () => {
    superuser.mockReturnValue(true);
    render(<GroupsPage client={client({ listGroups: async () => [] })} />);
    await userEvent.click(await screen.findByTestId("groups-new"));
    // The backdrop, not the panel — since #779 P5 the panel is ModalShell's
    // role="dialog" and the backdrop is its sibling.
    return screen.getByTestId("new-group-backdrop");
  };

  it("ignores a backdrop click once a name has been typed", async () => {
    const dialog = await openIt();
    await userEvent.type(screen.getByLabelText("Group name"), "Platform");

    await userEvent.click(dialog);

    expect(screen.getByLabelText("Group name")).toHaveValue("Platform");
  });

  it("still closes on a backdrop click while the form is untouched", async () => {
    const dialog = await openIt();
    await userEvent.click(dialog);
    expect(screen.queryByRole("dialog", { name: "New group" })).not.toBeInTheDocument();
  });
});
