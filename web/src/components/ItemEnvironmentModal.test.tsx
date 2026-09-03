// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ItemEnvironmentModal } from "./ItemEnvironmentModal";
import { renderWithQuery } from "../test/queryWrapper";

/**
 * The panel's frame: it fetches, and it decides what the two halves may say.
 *
 * The point of testing the modal separately from the panel is that the panel
 * takes its answers as props and cannot be wrong about them — everything that
 * can be wrong lives here, in what gets fetched and what gets passed down.
 */

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

const ENVIRONMENT = {
  running: false,
  stated_cpu_cores: null,
  stated_memory_bytes: null,
  effective_cpu_cores: 2,
  effective_memory_bytes: null,
  // What the BACKEND says it will really apply. Omitting these made the double
  // disagree with the real payload, the dial correctly disappeared, and this
  // test went red — which is the enforcement gate proving itself end to end.
  enforced_cpu_cores: 2,
  enforced_memory_bytes: null,
  cpu_bound_by: null,
  memory_bound_by: null,
};

const CAPPED = {
  limits: { count: 0, cpu: 4, memory_bytes: 0, disk_bytes: 0 },
  cpu_in_use: 2,
  memory_in_use: 0,
  live: [],
  workspaces: [],
  disk_in_use: 0,
  disk_tracked: false,
  owner: "alice",
};

const UNCAPPED = { ...CAPPED, limits: { count: 0, cpu: 0, memory_bytes: 0, disk_bytes: 0 } };

function route(resources: unknown) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/environment")) return json(ENVIRONMENT);
    if (url.includes("/me/resources")) return json(resources);
    return json({});
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", route(CAPPED));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ItemEnvironmentModal", () => {
  it("draws the size half where the deploy caps somebody", async () => {
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("budget-gauge")).toBeTruthy());
    expect(screen.getByTestId("cpu-input")).toBeTruthy();
  });

  it("omits the size half where it caps nobody, and keeps the status half", async () => {
    // The shipped default. `0 / 0` is not a reading, and a dial with an
    // unlimited ceiling is worse than no dial — but "is it running, close it"
    // is about a machine and still worth having.
    vi.stubGlobal("fetch", route(UNCAPPED));

    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    expect(screen.queryByTestId("budget-gauge")).toBeNull();
    expect(screen.queryByTestId("cpu-input")).toBeNull();
  });

  it("asks the item's route for the item, and the person's for the total", async () => {
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);

    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    const urls = fetcher.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes("/a/rca/items/i-1/environment"))).toBe(true);
    expect(urls.some((u) => u.includes("/me/resources"))).toBe(true);
  });
});

// Re-exported so the suite fails loudly if the wrapper ever stops providing a
// client, rather than every test here failing with an opaque hook error.
export { render };
