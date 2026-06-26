// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { renderWithQuery } from "../../test/queryWrapper";
import { UsageBar } from "./UsageBar";

beforeEach(() => vi.restoreAllMocks());
afterEach(cleanup);

describe("UsageBar (#245)", () => {
  it("shows used / quota when a quota is set", async () => {
    vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({
      used: 5 * 1024 * 1024 * 1024,
      quota: 20 * 1024 * 1024 * 1024,
    });
    renderWithQuery(<UsageBar slug="rca" itemId="it1" />);
    // Locale-agnostic: both formatted sizes appear in the bar's label.
    await waitFor(() => {
      const bar = screen.getByTestId("workspace-usage");
      expect(bar).toHaveTextContent("5.0 GB");
      expect(bar).toHaveTextContent("20.0 GB");
    });
  });

  it("is hidden when the workspace has no quota (0 = unlimited)", async () => {
    vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 123, quota: 0 });
    renderWithQuery(<UsageBar slug="rca" itemId="it1" />);
    // give the query a tick to resolve, then assert nothing rendered
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("workspace-usage")).toBeNull();
  });

  it("warns when the workspace is full", async () => {
    vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 1000, quota: 1000 });
    renderWithQuery(<UsageBar slug="rca" itemId="it1" />);
    await waitFor(() => expect(screen.getByText(/full|已滿/i)).toBeInTheDocument());
  });
});
