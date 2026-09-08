// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

/** Memory ENFORCED, and already set — the only shape in which the memory field
 *  is drawn at all, which is why nothing caught the format mismatch below. */
const WITH_MEMORY = {
  ...ENVIRONMENT,
  stated_memory_bytes: 536870912,
  effective_memory_bytes: 536870912,
  enforced_memory_bytes: 536870912,
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

function route(resources: unknown, environment: unknown = ENVIRONMENT) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/environment")) return json(environment);
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

  it("is a real modal — it dims what is behind it and answers Escape", async () => {
    // It used to be a bare `<div className="modal">`: nothing dimmed behind it,
    // focus stayed on the page underneath, and the only way out was one
    // unlabelled `×` below the panel.
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("environment-status")).toBeTruthy());
    expect(screen.getByTestId("item-environment-modal-backdrop")).toBeTruthy();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("keeps a size that was typed but not yet blurred", async () => {
    // Escape is NEW here, and the field commits on BLUR. The old `×` happened
    // to save on the way out — clicking it moved focus, which fired the blur —
    // so making this a proper modal is precisely what introduces a way to lose
    // a typed number. The guard goes in with the exit that needs it.
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.keyboard("{Escape}");

    expect(await screen.findByText("放棄未儲存的變更？")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes without asking when the person changed nothing", async () => {
    // The other half of the guard. A prompt that fires when nothing was edited
    // is the one that teaches people to click through it.
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.queryByText("放棄未儲存的變更？")).toBeNull();
  });

  it("does not ask about a value the ✕ just saved on its way out", async () => {
    // The fields commit on BLUR, and clicking the ✕ blurs. So the save goes out
    // first and the question comes second: the person is told their change is
    // unsaved, answers "discard", and it is persisted anyway. A guard that
    // fires over work that is already on its way to the server is the one that
    // teaches people to click straight through it.
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.click(screen.getByTestId("dismiss-item-environment"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.queryByText("放棄未儲存的變更？")).toBeNull();
  });

  it("counts a memory size as saved, though the field and the record spell it differently", async () => {
    // The field holds what the placeholder asks for — `1G`. The record holds
    // 1073741824. Measuring dirtiness by comparing those two STRINGS makes
    // every memory edit permanently unsaved, so from the first one onwards
    // every exit raises a prompt about work that is already stored.
    vi.stubGlobal("fetch", route(CAPPED, WITH_MEMORY));
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    const mem = await screen.findByTestId("memory-input");
    await userEvent.clear(mem);
    await userEvent.type(mem, "1G");
    await userEvent.tab();
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.queryByText("放棄未儲存的變更？")).toBeNull();
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
