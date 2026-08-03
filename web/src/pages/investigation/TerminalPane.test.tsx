// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../../test/queryWrapper";
import { TerminalPane } from "./TerminalPane";

// The empty-state help is the unit under test; the file-refresh side-effect
// (which needs a FileBufferProvider) is unrelated, so stub it to a no-op.
vi.mock("../../hooks/useRefreshFiles", () => ({ useRefreshFiles: () => () => {} }));

// The pane reaches for the shared `api` singleton, so the failure it must
// render is injected by mocking that module rather than by a prop.
const execShell = vi.fn();
vi.mock("../../api", () => ({ api: { execShell: (...a: unknown[]) => execShell(...a) } }));

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

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
