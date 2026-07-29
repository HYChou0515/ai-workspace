// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Onboarding } from "../api/types";
import { OnboardingModal } from "./OnboardingModal";

afterEach(cleanup);

const CONTENT: Onboarding = {
  version: "1",
  title: "Welcome to RCA",
  intro: "Investigate failures end to end.",
  points: [
    { title: "Add evidence", body: "Upload logs and data." },
    { title: "Ask the agent", body: "Rank suspect factors." },
  ],
};

function setup(over: Partial<Parameters<typeof OnboardingModal>[0]> = {}) {
  const onGotIt = vi.fn();
  const onDontShowAgain = vi.fn();
  render(
    <OnboardingModal
      content={CONTENT}
      onGotIt={onGotIt}
      onDontShowAgain={onDontShowAgain}
      {...over}
    />,
  );
  return { onGotIt, onDontShowAgain };
}

describe("OnboardingModal", () => {
  it("renders the title, intro, and every point", () => {
    setup();
    expect(screen.getByText("Welcome to RCA")).toBeInTheDocument();
    expect(screen.getByText("Investigate failures end to end.")).toBeInTheDocument();
    expect(screen.getByText("Add evidence")).toBeInTheDocument();
    expect(screen.getByText("Upload logs and data.")).toBeInTheDocument();
    expect(screen.getByText("Ask the agent")).toBeInTheDocument();
    expect(screen.getByText("Rank suspect factors.")).toBeInTheDocument();
  });

  it("is an accessible modal dialog labelled by its title", () => {
    setup();
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  });

  it("'Got it' invokes onGotIt (close-for-now)", () => {
    const { onGotIt, onDontShowAgain } = setup();
    fireEvent.click(screen.getByRole("button", { name: /got it/i }));
    expect(onGotIt).toHaveBeenCalledTimes(1);
    expect(onDontShowAgain).not.toHaveBeenCalled();
  });

  it("'Don't show again' invokes onDontShowAgain (permanent)", () => {
    const { onGotIt, onDontShowAgain } = setup();
    fireEvent.click(screen.getByRole("button", { name: /don't show again/i }));
    expect(onDontShowAgain).toHaveBeenCalledTimes(1);
    expect(onGotIt).not.toHaveBeenCalled();
  });

  it("Escape closes for now (onGotIt), never a permanent dismiss", () => {
    const { onGotIt, onDontShowAgain } = setup();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onGotIt).toHaveBeenCalledTimes(1);
    expect(onDontShowAgain).not.toHaveBeenCalled();
  });

  it("offers a link to the full help page when onSeeFull is provided (#230)", () => {
    const onSeeFull = vi.fn();
    setup({ onSeeFull });
    fireEvent.click(screen.getByText(/See the full guide/));
    expect(onSeeFull).toHaveBeenCalledTimes(1);
  });

  it("omits the full-guide link when onSeeFull is not provided (#230)", () => {
    setup();
    expect(screen.queryByText(/See the full guide/)).not.toBeInTheDocument();
  });
});

/**
 * #fe-responsive — measured in a real browser at 390x844: the three action
 * buttons sit in one `space-between` row, each pinned to `height: 32` with no
 * wrap control. On a narrow modal the labels wrapped to a second line inside
 * that fixed height and were clipped mid-glyph — "Got it" lost its lower half.
 * Same defect family as the workspace's bottom-panel tab row.
 */
describe("OnboardingModal actions survive a narrow modal (#fe-responsive)", () => {
  it("keeps each action label on one line", () => {
    setup();
    for (const label of ["Don't show again", "Got it"]) {
      const btn = screen.getByRole("button", { name: label });
      expect(btn.style.whiteSpace).toBe("nowrap");
      expect(btn.style.flexShrink).toBe("0");
    }
  });

  it("lets the action row wrap so the buttons keep their full height", () => {
    setup();
    const row = screen.getByTestId("onboarding-actions");
    expect(row.style.flexWrap).toBe("wrap");
  });
});
