// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [
    {
      id: "alice",
      name: "Alice",
      section: "Eng",
      email: "a@x",
      photo_url: null,
    },
    {
      id: "carol",
      name: "Carol",
      section: "Eng",
      email: "c@x",
      photo_url: null,
    },
  ],
}));
vi.mock("./UserChip", () => ({
  UserChip: ({ userId }: { userId: string }) => <span>{userId}</span>,
  UserAvatar: ({ userId }: { userId: string }) => <span>{userId}</span>,
}));
vi.mock("./Icon", () => ({ Icon: () => <span /> }));

import { LocaleProvider, setStoredLocale, translate } from "../lib/i18n";
import { DOC_ROLES, type CollectionPermission } from "../lib/permission";
import { renderWithQuery } from "../test/queryWrapper";
import { PermissionDialog } from "./PermissionDialog";

afterEach(cleanup);

describe("PermissionDialog — the viewer's language (plan-skill-hub-ui-polish D12)", () => {
  // No LocaleProvider here: `useT` renders zh-TW, the primary audience.
  it("speaks zh-TW, and says who 'Public' reaches as the caller names it", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="alice/csv-peek"
        owner="alice"
        value={perm()}
        audience="platform"
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("分享「alice/csv-peek」")).toBeInTheDocument();
    expect(screen.getByText("私人")).toBeInTheDocument();
    expect(screen.getByText("限定")).toBeInTheDocument();
    expect(screen.getByText("公開")).toBeInTheDocument();
    expect(screen.getByText("平台上所有人")).toBeInTheDocument();
    expect(screen.getByTestId("permission-cancel")).toHaveTextContent("取消");
    expect(screen.getByTestId("permission-save")).toHaveTextContent("儲存");
  });

  it("defaults the audience to the workspace — the item and collection callers' meaning", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm()}
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("這個 workspace 的所有人")).toBeInTheDocument();
    expect(screen.queryByText("平台上所有人")).toBeNull();
  });

  // Parity with the dialog as it was: in English, every caller that passed
  // no `audience` reads exactly the words it read before this change —
  // rendered through the same props each existing caller passes.
  it.each([
    [
      "KbCollectionPage",
      { resourceName: "Docs", roles: undefined, caption: undefined },
    ],
    [
      "KbDocIde",
      {
        resourceName: "notes/a.md",
        roles: DOC_ROLES,
        caption:
          "Choose who can read this document. It can only restrict access further than the collection — never widen it.",
      },
    ],
  ])("reads in English exactly as before for %s", (_caller, props) => {
    setStoredLocale("en");
    renderWithQuery(
      <LocaleProvider>
        <PermissionDialog
          owner="bob"
          value={perm()}
          onSubmit={() => {}}
          onClose={() => {}}
          {...props}
        />
      </LocaleProvider>,
    );
    expect(
      screen.getByText(`Share “${props.resourceName}”`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        props.caption ?? "Choose who can access this collection.",
      ),
    ).toBeInTheDocument();
    for (const [label, hint] of [
      ["Private", "Only you"],
      ["Restricted", "You + specific people"],
      ["Public", "Everyone in the workspace"],
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(screen.getByText(hint)).toBeInTheDocument();
    }
    expect(screen.getByTestId("toggle-advanced")).toHaveTextContent(
      "Show advanced",
    );
    expect(screen.getByTestId("permission-cancel")).toHaveTextContent("Cancel");
    expect(screen.getByTestId("permission-save")).toHaveTextContent("Save");
    localStorage.removeItem("ws.locale");
  });
});

const perm = (
  over: Partial<CollectionPermission> = {},
): CollectionPermission => ({
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

const shared = () =>
  perm({ read_meta: ["user:alice"], read_content: ["user:alice"] });

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
    fireEvent.change(screen.getByTestId("role-alice"), {
      target: { value: "editor" },
    });
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
    expect((onSubmit.mock.calls[0][0] as CollectionPermission).visibility).toBe(
      "public",
    );
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
    fireEvent.click(
      screen.getByRole("button", {
        name: translate("zh-TW", "perm.remove", { name: "alice" }),
      }),
    );
    fireEvent.click(screen.getByTestId("permission-save"));
    expect(
      (onSubmit.mock.calls[0][0] as CollectionPermission).read_content,
    ).toEqual([]);
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
    expect(screen.getByTestId("advanced-verbs").textContent).toContain(
      "read_content: user:alice",
    );
  });

  // #460 P6 — the advanced preview must follow the SELECTED visibility, not echo
  // the stored Restricted grant list for every mode.
  it("recomputes the advanced preview from the selected visibility", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm({
          read_meta: ["user:alice"],
          read_content: ["user:alice"],
          change_permission: ["user:carol"],
        })}
        onSubmit={() => {}}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("toggle-advanced"));
    // Restricted (default): named grants.
    expect(screen.getByTestId("advanced-verbs").textContent).toContain(
      "read_content: user:alice",
    );

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
    expect(
      screen.getByText("Tighten who can read this document."),
    ).toBeInTheDocument();
  });
});

describe("PermissionDialog — group grants (#608)", () => {
  const pickable = [
    {
      resource_id: "eng",
      name: "Engineering",
      description: "",
      member_count: 12,
    },
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

  it("keeps groups behind their own tab instead of stacking them under the people list", () => {
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm({
          read_meta: ["user:alice"],
          read_content: ["user:alice"],
        })}
        pickableGroups={pickable}
        onSubmit={vi.fn()}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByTestId("group-grant-select")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("share-tab-groups"));
    expect(screen.getByTestId("group-grant-select")).toBeInTheDocument();
    expect(
      screen.queryByTestId("permission-people-picker"),
    ).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByTestId("share-tab-groups"));
    fireEvent.click(screen.getByTestId("group-picker-item-eng"));
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
        value={perm({
          read_meta: ["group:ghost"],
          read_content: ["group:ghost"],
        })}
        pickableGroups={[
          {
            resource_id: "eng",
            name: "Engineering",
            description: "",
            member_count: 2,
          },
        ]}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByText(translate("zh-TW", "perm.group.unknown")),
    ).toBeInTheDocument();
    expect(screen.getByTestId("group-remove-ghost")).toBeInTheDocument();
  });
});

/* Same panel shape as ItemShareDialog, same failure: the flex column compresses
 * whatever it may, and the only compressible child is the "Add people…" picker
 * (a scroll container), so a handful of grants collapsed it to a sliver that no
 * scrollbar could bring back. Reached from a KB collection and the doc IDE. */
describe("PermissionDialog layout — a long grant list must not eat the picker", () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => `user:p${i}`);
  const open = () =>
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={perm({ read_meta: many(6), read_content: many(6) })}
        onSubmit={vi.fn()}
        onClose={() => {}}
      />,
    );

  it("refuses to let the flex column compress the people section", () => {
    open();
    expect(screen.getByTestId("permission-grants").style.flexShrink).toBe("0");
  });

  it("does not wrap the picker in a second scroll layer that hides its search box", () => {
    open();
    expect(screen.getByTestId("permission-people-picker").style.overflow).toBe(
      "",
    );
  });

  it("keeps Save in the pinned action bar so eight grants cannot scroll it away", () => {
    open();
    expect(
      screen
        .getByTestId("permission-save")
        .closest('[data-testid="modal-actions"]'),
    ).not.toBeNull();
  });

  it("scrolls a long grant list in its own box instead of pushing the picker away", () => {
    open();
    const list = screen.getByTestId("grant-list");
    expect(list.style.overflow).toBe("auto");
    expect(list.style.maxHeight).not.toBe("");
  });
});

describe("PermissionDialog — leaving with an unsent change (#779)", () => {
  it("asks before dropping it, and keeps the picks", async () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={shared()}
        onSubmit={onSubmit}
        onClose={onClose}
      />,
    );
    fireEvent.change(screen.getByTestId("role-alice"), {
      target: { value: "editor" },
    });

    fireEvent.click(screen.getByTestId("permission-cancel"));

    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect((screen.getByTestId("role-alice") as HTMLSelectElement).value).toBe(
      "editor",
    );
  });

  it("closes without asking when the access was left as found", () => {
    const onClose = vi.fn();
    renderWithQuery(
      <PermissionDialog
        resourceName="Docs"
        owner="bob"
        value={shared()}
        onSubmit={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("permission-cancel"));
    expect(onClose).toHaveBeenCalled();
  });
});
