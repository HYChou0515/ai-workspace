// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [
    { id: "alice", name: "Alice", section: "Eng", email: "a@x", photo_url: null },
    { id: "carol", name: "Carol", section: "Eng", email: "c@x", photo_url: null },
  ],
}));
vi.mock("./UserChip", () => ({
  UserChip: ({ userId }: { userId: string }) => <span>{userId}</span>,
  UserAvatar: ({ userId }: { userId: string }) => <span>{userId}</span>,
}));
vi.mock("./Icon", () => ({ Icon: () => <span /> }));

import { DOC_ROLES, type CollectionPermission } from "../lib/permission";
import { renderWithQuery } from "../test/queryWrapper";
import { PermissionDialog } from "./PermissionDialog";

afterEach(cleanup);

const perm = (over: Partial<CollectionPermission> = {}): CollectionPermission => ({
  visibility: "restricted",
  read_meta: [],
  write_meta: [],
  read_content: [],
  add_content: [],
  edit_content: [],
  read_chat: [],
  converse: [],
  execute: [],
  use_terminal: [],
  change_permission: [],
  ...over,
});

const shared = () => perm({ read_meta: ["user:alice"], read_content: ["user:alice"] });

describe("PermissionDialog", () => {
  it("pre-fills grants from the current permission and saves the edited role", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={shared()}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    // alice is decoded as a Viewer grant
    expect(screen.getByTestId("role-alice")).toHaveValue("viewer");
    // promote to Editor and save → the encoded permission grants edit_content
    fireEvent.change(screen.getByTestId("role-alice"), { target: { value: "editor" } });
    fireEvent.click(screen.getByTestId("permission-save"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const saved = onSubmit.mock.calls[0][0] as CollectionPermission;
    expect(saved.edit_content).toEqual(["user:alice"]);
    expect(saved.visibility).toBe("restricted");
  });

  it("switches visibility to public", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm()}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("visibility-public"));
    fireEvent.click(screen.getByTestId("permission-save"));
    expect((onSubmit.mock.calls[0][0] as CollectionPermission).visibility).toBe("public");
  });

  it("removes a grantee", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={shared()}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove alice" }));
    fireEvent.click(screen.getByTestId("permission-save"));
    expect((onSubmit.mock.calls[0][0] as CollectionPermission).read_content).toEqual([]);
  });

  it("shows the raw verb grants under Advanced", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={shared()}
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("toggle-advanced"));
    expect(screen.getByTestId("advanced-verbs").textContent).toContain("read_content: user:alice");
  });

  // #460 P6 — the advanced preview must follow the SELECTED visibility, not echo
  // the stored Restricted grant list for every mode.
  it("recomputes the advanced preview from the selected visibility", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm({ read_meta: ["user:alice"], read_content: ["user:alice"], change_permission: ["user:carol"] })}
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("toggle-advanced"));
    // Restricted (default): named grants.
    expect(screen.getByTestId("advanced-verbs").textContent).toContain("read_content: user:alice");

    // Public: everyone — except change_permission, which stays grant-list only.
    fireEvent.click(screen.getByTestId("visibility-public"));
    const pub = screen.getByTestId("advanced-verbs").textContent ?? "";
    expect(pub).toContain("read_meta: everyone");
    expect(pub).not.toContain("read_meta: user:alice");
    expect(pub).toContain("change_permission: user:carol");

    // Private: nobody — but change_permission still shows its grant list.
    fireEvent.click(screen.getByTestId("visibility-private"));
    const priv = screen.getByTestId("advanced-verbs").textContent ?? "";
    expect(priv).toContain("read_meta: —");
    expect(priv).toContain("change_permission: user:carol");
  });

  // #308 — the per-doc override reuses this dialog with a narrower role set + copy.
  it("restricts the role picker to the roles it is given (DOC_ROLES = Viewer only)", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="notes.md"
        owner="bob"
        value={shared()}
        roles={DOC_ROLES}
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    const options = Array.from(
      (screen.getByTestId("role-alice") as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(options).toEqual(["viewer"]);
  });

  it("renders the caller-supplied caption", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="notes.md"
        owner="bob"
        value={perm()}
        caption="Tighten who can read this document."
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Tighten who can read this document.")).toBeInTheDocument();
  });
});

describe("PermissionDialog — group grants (#608)", () => {
  const pickable = [
    { resource_id: "eng", name: "Engineering", description: "", member_count: 12 },
    { resource_id: "hr", name: "HR", description: "", member_count: 4 },
  ];

  it("shows an existing group grant by name and keeps it on save", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm({ read_meta: ["group:eng"], read_content: ["group:eng"] })}
        pickableGroups={pickable}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Engineering")).toBeInTheDocument(); // resolved name, not the id
    expect(screen.getByTestId("group-role-eng")).toHaveValue("viewer");
    fireEvent.click(screen.getByTestId("permission-save"));
    const saved = onSubmit.mock.calls[0][0] as CollectionPermission;
    expect(saved.read_content).toEqual(["group:eng"]); // round-tripped, not wiped
  });

  it("adds a group grant from the picker", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm()}
        pickableGroups={pickable}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("group-grant-select"), { target: { value: "eng" } });
    fireEvent.click(screen.getByTestId("permission-save"));
    const saved = onSubmit.mock.calls[0][0] as CollectionPermission;
    expect(saved.read_meta).toContain("group:eng");
  });

  it("removes a group grant", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm({ read_meta: ["group:eng"], read_content: ["group:eng"] })}
        pickableGroups={pickable}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("group-remove-eng"));
    fireEvent.click(screen.getByTestId("permission-save"));
    const saved = onSubmit.mock.calls[0][0] as CollectionPermission;
    expect(saved.read_meta).not.toContain("group:eng");
  });
});

describe("PermissionDialog — unresolvable group grant (#608)", () => {
  it("labels a group we can't resolve as 'Unknown group' (still removable)", () => {
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        // a grant to a group that isn't in the pickable list (deleted / not visible)
        value={perm({ read_meta: ["group:ghost"], read_content: ["group:ghost"] })}
        pickableGroups={[{ resource_id: "eng", name: "Engineering", description: "", member_count: 2 }]}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Unknown group")).toBeInTheDocument();
    expect(screen.getByTestId("group-remove-ghost")).toBeInTheDocument();
  });
});
