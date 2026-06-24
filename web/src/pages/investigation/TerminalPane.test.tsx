// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../../test/queryWrapper";
import { TerminalPane } from "./TerminalPane";

// The empty-state help is the unit under test; the file-refresh side-effect
// (which needs a FileBufferProvider) is unrelated, so stub it to a no-op.
vi.mock("../../hooks/useRefreshFiles", () => ({ useRefreshFiles: () => () => {} }));

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
