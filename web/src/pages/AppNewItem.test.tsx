// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { AppNewItem } from "./AppNewItem";

afterEach(cleanup);

const createAppItem = vi.fn().mockResolvedValue({ resource_id: "rca-investigation/1" });
const navigate = vi.fn();

vi.mock("../api", () => ({ api: { createAppItem: (...a: unknown[]) => createAppItem(...a) } }));
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: () => "default-user" }));
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [],
  useUser: (id: string) => ({ id, name: id, section: "", email: "", photo_url: null }),
}));
vi.mock("../hooks/useResources", () => ({
  useAppManifest: () => ({
    item: { noun: "Investigation", create_label: "Start Investigation" },
    layout: { breadcrumb: [], statusbar: [], list: [], form: ["severity", "product"], default_tabs: [] },
    fields: [
      { name: "title", label: "Title", kind: "text" },
      { name: "description", label: "Description", kind: "text" },
      { name: "severity", label: "Severity", kind: "select", options: ["P0", "P2"] },
      { name: "product", label: "Product", kind: "text" },
    ],
    labels: {},
    default_profile: "default",
    profiles: [
      { name: "default", title: "Default", description: "" },
      { name: "tool-demo", title: "Tool demo", description: "" },
    ],
  }),
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigate,
  useParams: () => ({ slug: "rca" }),
}));

describe("AppNewItem", () => {
  it("creates with the title + the default profile, then navigates into the new item", async () => {
    render(
      <QueryWrap>
        <MemoryRouter>
          <AppNewItem />
        </MemoryRouter>
      </QueryWrap>,
    );
    await userEvent.type(screen.getByLabelText(/title/i), "Oven drift");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(createAppItem).toHaveBeenCalledWith("rca", {
        title: "Oven drift",
        profile: "default",
      }),
    );
    // #4: goes straight into the new item's workspace (id percent-encoded).
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/a/rca/rca-investigation%2F1"));
  });

  it("creates with the picked profile when the App ships more than one", async () => {
    render(
      <QueryWrap>
        <MemoryRouter>
          <AppNewItem />
        </MemoryRouter>
      </QueryWrap>,
    );
    await userEvent.type(screen.getByLabelText(/title/i), "Oven drift");
    // profiles render as selectable template cards — pick "Tool demo"
    await userEvent.click(screen.getByRole("button", { name: /tool demo/i }));
    await userEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(createAppItem).toHaveBeenCalledWith("rca", {
        title: "Oven drift",
        profile: "tool-demo",
      }),
    );
  });
});
