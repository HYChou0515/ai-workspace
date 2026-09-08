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

function route(
  resources: unknown,
  environment: unknown = ENVIRONMENT,
  { refuseSave = false } = {},
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PUT") {
      return refuseSave
        ? new Response(JSON.stringify({ detail: "sandbox_quota_exceeded" }), { status: 507 })
        : json({});
    }
    if (url.includes("/environment")) return json(environment);
    if (url.includes("/me/resources")) return json(resources);
    return json({});
  });
}

/** What actually reached the server — the only evidence that a number the
 *  person typed was not dropped. Asserting on the absence of a prompt cannot
 *  tell a saved value from a lost one. */
const puts = (f: ReturnType<typeof route>) =>
  f.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT")
    .map((c) => String((c[1] as RequestInit).body));

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

  /**
   * This panel APPLIES AS YOU GO — the fields commit on blur and there is no
   * Save button to press, which is the "live-applying picker" the modal rules
   * name as having nothing to lose. Two review rounds went into trying to make
   * it behave like a form instead, and each fix uncovered another exit where
   * the prompt asked about a value it had already saved: the ✕ blurs on its way
   * out, and so does the confirm dialog itself, because it takes focus in order
   * to be answered. You cannot ask "discard this?" with a question whose asking
   * commits it.
   *
   * So leaving COMMITS, on every exit, and nothing is asked. These tests count
   * what reached the server, because a prompt that did not appear is equally
   * consistent with a number that was saved and one that was thrown away.
   */
  it("sends a size that was typed but never blurred, when Escape closes it", async () => {
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(puts(fetcher)).toEqual([JSON.stringify({ cpu_cores: 3, memory: null })]);
    expect(screen.queryByText("放棄未儲存的變更？")).toBeNull();
  });

  it("sends it exactly once when the ✕ is the way out", async () => {
    // What this pins is EXACTLY ONCE. happy-dom blurs on click the way Chrome
    // does, so it would stay green with the explicit blur removed — the test
    // that pins that is the Escape one above, which is red without it. The
    // cross-browser half (Firefox and Safari do not blur on mousedown, so the
    // ✕ alone would lose the number there) is not reachable from this runner
    // and is why the blur is explicit rather than left to the click.
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.click(screen.getByTestId("dismiss-item-environment"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(puts(fetcher)).toEqual([JSON.stringify({ cpu_cores: 3, memory: null })]);
  });

  it("sends nothing at all when the person typed nothing", async () => {
    // The other side of it. A panel that PUTs on every close would spend a
    // request — and a quota check — on someone who only came to look.
    const fetcher = route(CAPPED);
    vi.stubGlobal("fetch", fetcher);
    const onClose = vi.fn();
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={onClose} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(puts(fetcher)).toEqual([]);
  });

  it("sends a memory size in the spelling the field asks for", async () => {
    const fetcher = route(CAPPED, WITH_MEMORY);
    vi.stubGlobal("fetch", fetcher);
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    const mem = await screen.findByTestId("memory-input");
    await userEvent.clear(mem);
    await userEvent.type(mem, "1G");
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(puts(fetcher).length).toBe(1));
    expect(JSON.parse(puts(fetcher)[0]).memory).toBe("1G");
    // Without this the test also passes the OLD way: the confirm dialog took
    // focus, which blurred the field, which saved. Same PUT, opposite meaning.
    expect(screen.queryByText("放棄未儲存的變更？")).toBeNull();
  });

  it("says so when the server refused the size, rather than dropping it in silence", async () => {
    // Committing on the way out is only safe if a refusal is visible. The quota
    // this spends belongs to the item's OWNER, so 507 is a normal answer here,
    // and a panel that swallowed it would leave someone certain they had set a
    // number that was never stored.
    vi.stubGlobal("fetch", route(CAPPED, ENVIRONMENT, { refuseSave: true }));
    renderWithQuery(
      <ItemEnvironmentModal slug="rca" itemId="i-1" canEdit onClose={() => {}} />,
    );

    await waitFor(() => expect(screen.getByTestId("cpu-input")).toBeTruthy());
    await userEvent.type(screen.getByTestId("cpu-input"), "3");
    await userEvent.tab();

    expect(await screen.findByTestId("save-failed")).toBeTruthy();
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
