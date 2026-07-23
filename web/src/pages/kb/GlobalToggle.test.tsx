// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { KbApi, KbCollection } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { GlobalToggle } from "./GlobalToggle";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  is_global: false,
  auto_digest: false,
  ...over,
});

function client(over: Partial<KbApi> = {}): KbApi {
  return { ...mockKbApi, ...over };
}

describe("GlobalToggle", () => {
  it("is hidden for a non-superuser", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ id: "u", is_superuser: false, groups: [] });
    render(<GlobalToggle collection={coll({})} client={client()} />);
    // The query resolves to is_superuser:false → the control never renders.
    await waitFor(() => expect(api.getMe).toHaveBeenCalled());
    expect(screen.queryByTestId("kb-global-toggle")).not.toBeInTheDocument();
  });

  it("shows the toggle for a superuser, reflecting is_global", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ id: "u", is_superuser: true, groups: [] });
    render(<GlobalToggle collection={coll({ is_global: true })} client={client()} />);
    const box = await screen.findByTestId("kb-global-toggle");
    expect(box).toBeChecked();
  });

  it("calls setCollectionGlobal on change (superuser)", async () => {
    vi.spyOn(api, "getMe").mockResolvedValue({ id: "u", is_superuser: true, groups: [] });
    const setCollectionGlobal = vi.fn(async (id: string, v: boolean) => ({
      resource_id: id,
      is_global: v,
    }));
    render(
      <GlobalToggle
        collection={coll({ resource_id: "c9", is_global: false })}
        client={client({ setCollectionGlobal })}
      />,
    );
    const box = await screen.findByTestId("kb-global-toggle");
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    await waitFor(() => expect(setCollectionGlobal).toHaveBeenCalledWith("c9", true));
  });
});
