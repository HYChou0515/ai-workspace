// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { QueryWrap } from "../../test/queryWrapper";
import { TerminalPane } from "./TerminalPane";

// The empty-state help is the unit under test; the file-refresh side-effect
// (which needs a FileBufferProvider) is unrelated, so stub it to a no-op.
vi.mock("../../hooks/useRefreshFiles", () => ({ useRefreshFiles: () => () => {} }));

// The pane reaches for the shared `api` singleton, so the failure it must
// render is injected by mocking that module rather than by a prop.
const execShell = vi.fn();
vi.mock("../../api", () => ({ api: { execShell: (...a: unknown[]) => execShell(...a) } }));

// A quota refusal routes the user to /my-resources (#692), so the pane renders
// a real router link.
const Wrap = ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter>
    <QueryWrap>{children}</QueryWrap>
  </MemoryRouter>
);

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: Wrap });

describe("TerminalPane empty-state help (#171)", () => {
  afterEach(cleanup);

  it("describes the execution environment, not a 'sandbox'", () => {
    render(<TerminalPane investigationId="item:1" />);
    // zh-TW default (no LocaleProvider): de-jargoned to 執行環境.
    expect(screen.getByText(/執行環境/)).toBeInTheDocument();
    expect(screen.queryByText(/sandbox/i)).not.toBeInTheDocument();
  });
});


describe("TerminalPane quota refusals", () => {
  afterEach(cleanup);

  // The terminal wakes a sandbox, so it is one of only two surfaces where the
  // live-environment limit can appear at all. Before this, all three quotas
  // rendered as the raw "exec failed: 507" — a status with no remedy attached.
  it.each([
    ["sandbox_quota_exceeded", /執行環境已達上限/],
    ["user_quota_exceeded", /空間總量已滿/],
    ["workspace_quota_exceeded", /工作區空間已滿/],
  ])("names the limit for %s", async (code, expected) => {
    execShell.mockRejectedValueOnce(
      Object.assign(new Error("exec failed: 507"), { status: 507, code }),
    );
    render(<TerminalPane investigationId="item:1" />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "ls{Enter}");
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("still reports a non-quota failure as itself", async () => {
    execShell.mockRejectedValueOnce(Object.assign(new Error("boom"), { status: 500 }));
    render(<TerminalPane investigationId="item:1" />);
    await userEvent.type(screen.getByRole("textbox"), "ls{Enter}");
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});

describe("TerminalPane quota refusals are reachable (#692)", () => {
  afterEach(cleanup);

  // The refusal already said where to go. Saying it in a scrollback line the
  // user cannot press means the remedy is a name they have to go and find in
  // the global-nav popover — which is the whole gap #692 is about.
  it.each([["sandbox_quota_exceeded"], ["user_quota_exceeded"]])(
    "makes the remedy in the %s message pressable",
    async (code) => {
      execShell.mockRejectedValueOnce(
        Object.assign(new Error("exec failed: 507"), { status: 507, code }),
      );
      render(<TerminalPane investigationId="item:1" />);
      await userEvent.type(screen.getByRole("textbox"), "ls{Enter}");
      expect(await screen.findByRole("link", { name: "我的資源" })).toHaveAttribute(
        "href",
        "/my-resources",
      );
    },
  );

  // This item's workspace is full: the files to delete are right here, so there
  // is nothing on /my-resources to do and no link to offer.
  it("offers no link when the fix is in this workspace", async () => {
    execShell.mockRejectedValueOnce(
      Object.assign(new Error("exec failed: 507"), {
        status: 507,
        code: "workspace_quota_exceeded",
      }),
    );
    render(<TerminalPane investigationId="item:1" />);
    await userEvent.type(screen.getByRole("textbox"), "ls{Enter}");
    expect(await screen.findByText(/工作區空間已滿/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
