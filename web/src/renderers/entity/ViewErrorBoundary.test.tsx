// @vitest-environment happy-dom
/**
 * The boundary's reset contract (#698 round 2).
 *
 * Resetting on `kind` alone was not enough. The IDE reuses one renderer across
 * files, so a crash could follow the user to a different file of the same kind,
 * and — worse — repairing a file IN PLACE (an agent write, or the gear panel's
 * own save) never cleared the error, leaving a fixed file broken for the life
 * of the tab. Both cases come down to: a new `resetKey` means try again.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ViewErrorBoundary } from "./ViewErrorBoundary";

// React logs caught errors; keep the run readable without hiding real failures.
beforeEach(() => vi.spyOn(console, "error").mockImplementation(() => {}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Boom({ explode }: { explode: boolean }) {
  if (explode) throw new Error("kaboom");
  return <div data-testid="ok">fine</div>;
}

describe("ViewErrorBoundary", () => {
  it("renders its child when nothing throws", () => {
    render(
      <ViewErrorBoundary kind="acme" resetKey="a">
        <Boom explode={false} />
      </ViewErrorBoundary>,
    );
    expect(screen.getByTestId("ok")).toBeInTheDocument();
  });

  it("shows the kind and the message instead of unmounting", () => {
    render(
      <ViewErrorBoundary kind="acme" resetKey="a">
        <Boom explode />
      </ViewErrorBoundary>,
    );
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("acme");
    expect(banner).toHaveTextContent("kaboom");
  });

  it("retries when the resetKey changes — the in-place repair case", () => {
    const { rerender } = render(
      <ViewErrorBoundary kind="acme" resetKey="v1">
        <Boom explode />
      </ViewErrorBoundary>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();

    // Same kind, same file — only the CONTENTS changed, because someone fixed it.
    rerender(
      <ViewErrorBoundary kind="acme" resetKey="v2">
        <Boom explode={false} />
      </ViewErrorBoundary>,
    );
    expect(screen.getByTestId("ok")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  // A file-reading plug-in's data lives in files the boundary knows nothing
  // about, so `resetKey` cannot cover them. Repairing one of those has to have
  // an escape that doesn't require closing the tab.
  it("re-reads the workspace before re-attempting, then recovers", async () => {
    let explode = true;
    function Flaky() {
      if (explode) throw new Error("kaboom");
      return <div data-testid="ok">fine</div>;
    }
    // Standing in for `useRefreshFiles`: the repair happened outside this tab,
    // so the buffer cache still holds the bad bytes. Re-rendering alone would
    // just throw again — the refresh is what makes Retry mean anything.
    const onRetry = vi.fn(async () => {
      explode = false;
    });

    render(
      <ViewErrorBoundary kind="acme" resetKey="unchanged" onRetry={onRetry}>
        <Flaky />
      </ViewErrorBoundary>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByTestId("ok")).toBeInTheDocument();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("catches again — and stays usable — when the retry doesn't help", async () => {
    const onRetry = vi.fn(async () => {});
    render(
      <ViewErrorBoundary kind="acme" resetKey="unchanged" onRetry={onRetry}>
        <Boom explode />
      </ViewErrorBoundary>,
    );
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    // still broken, but the escape hatch is offerable again rather than a dead
    // panel with a permanently disabled button
    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled());
    expect(screen.getByRole("status")).toHaveTextContent("kaboom");
  });

  it("carries the house button chrome, so the one escape hatch looks like a control", () => {
    render(
      <ViewErrorBoundary kind="acme" resetKey="a">
        <Boom explode />
      </ViewErrorBoundary>,
    );
    // base.css strips all default button styling; `.btn` is how this app puts it
    // back. Without it the only affordance renders as plain body text.
    expect(screen.getByRole("button", { name: /retry/i })).toHaveClass("btn");
  });

  it("stays broken while the resetKey is unchanged, rather than looping on a still-broken view", () => {
    const { rerender } = render(
      <ViewErrorBoundary kind="acme" resetKey="v1">
        <Boom explode />
      </ViewErrorBoundary>,
    );
    rerender(
      <ViewErrorBoundary kind="acme" resetKey="v1">
        <Boom explode />
      </ViewErrorBoundary>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
