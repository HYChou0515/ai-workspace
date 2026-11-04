// @vitest-environment happy-dom
/** docs/plan-wui-user.md D7: the banner offers 申請存取 through #968's request
 * flow. Someone who sees it is inside the workspace already, where that flow
 * notifies nobody (`already_readable`), so it says who can raise their role. */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { QueryWrap } from "../../test/queryWrapper";
import { WuiUserNeeded } from "./WuiUserNeeded";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WuiUserNeeded", () => {
  it("offers 申請存取 through the item's request flow", async () => {
    const ask = vi.spyOn(api, "requestItemAccess").mockResolvedValue({ item_id: "i1", requested: true, already_readable: false });
    render(
      <QueryWrap>
        <WuiUserNeeded slug="playground" itemId="i1" />
      </QueryWrap>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /申請存取|request access/i }));
    expect(ask).toHaveBeenCalledWith("playground", "i1");
  });

  it("says who can raise the role when the request flow notifies nobody", async () => {
    vi.spyOn(api, "requestItemAccess").mockResolvedValue({ item_id: "i1", requested: false, already_readable: true });
    render(
      <QueryWrap>
        <WuiUserNeeded slug="playground" itemId="i1" />
      </QueryWrap>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /申請存取|request access/i }));
    expect(await screen.findByText(/WUI User/)).toBeInTheDocument();
    expect(screen.queryByText(/讀檔權限/)).toBeNull();
  });
});

describe("an owner not known yet", () => {
  it("is named in the reader's language, not in English", async () => {
    vi.spyOn(api, "requestItemAccess").mockResolvedValue({ item_id: "i1", requested: false, already_readable: true });
    render(
      <QueryWrap>
        <WuiUserNeeded slug="playground" itemId="i1" />
      </QueryWrap>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /申請存取|request access/i }));
    const banner = await screen.findByTestId("wui-needs-wui-user");
    await screen.findByText(/已經在這個 workspace 裡|already in this workspace/);
    expect(banner.textContent).not.toMatch(/the owner/);
  });
});
