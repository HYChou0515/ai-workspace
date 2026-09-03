// @vitest-environment happy-dom
/**
 * The item page's environment panel.
 *
 * Two halves with different preconditions, because they answer to different
 * things. "Is my environment running, close it" is worth having on a deploy
 * that caps nobody — it is about a machine, not a budget. "How much of my
 * budget is this spending, and how big may it be" only means anything where a
 * budget exists; drawing `0 / 0` there is noise, and offering a dial whose
 * ceiling is unlimited is worse.
 *
 * The conditions below are split the way the previous plan's post-mortem asked
 * for: what is VISIBLE gets its own assertions, separate from what is
 * CLICKABLE. That plan passed its "clickable" conditions with a page that had
 * no stylesheet at all.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

// Unmount between tests. Leaked DOM makes a later query match twice and report
// "found multiple elements" — which reads as a product bug and is not one.
afterEach(cleanup);

import { ItemEnvironmentPanel } from "./ItemEnvironmentPanel";

const IDLE = {
  running: false,
  statedCpuCores: null,
  statedMemoryBytes: null,
  effectiveCpuCores: 2,
  effectiveMemoryBytes: 2 * 1024 ** 3,
  enforcedCpuCores: 2,
  enforcedMemoryBytes: 2 * 1024 ** 3,
  // Which ceiling bound, from the SERVER — the panel no longer infers it by
  // comparing the viewer's quota with a clamp made against the owner's.
  boundBy: null,
};

const BUDGET = { cpu: 4, memoryBytes: 8 * 1024 ** 3, cpuInUse: 2, memoryInUse: 2 * 1024 ** 3 };

describe("what is visible", () => {
  it("shows an unset size as the resolved number AND says it is the default", () => {
    // The condition the user made the price of never storing a default: an
    // empty field must not render as `0`, and must not render as a blank box
    // either — one reads as "unlimited", the other as "broken".
    render(<ItemEnvironmentPanel env={IDLE} budget={BUDGET} canEdit />);

    const shown = screen.getByTestId("cpu-value").textContent ?? "";
    expect(shown).toContain("2");
    expect(shown).not.toBe("0");
    expect(screen.getByTestId("cpu-origin").textContent).toMatch(/預設/);
  });

  it("tells a stated size apart from an inherited one at a glance", () => {
    render(
      <ItemEnvironmentPanel
        env={{ ...IDLE, statedCpuCores: 1 }}
        budget={BUDGET}
        canEdit
      />,
    );

    expect(screen.getByTestId("cpu-origin").textContent).not.toMatch(/預設/);
    // And a way back — otherwise "I set this once" is a one-way door.
    expect(screen.getByTestId("reset-cpu")).toBeTruthy();
  });

  it("shows BOTH numbers when a setting is held down, and names what held it", () => {
    render(
      <ItemEnvironmentPanel
        env={{ ...IDLE, statedCpuCores: 8, effectiveCpuCores: 4, boundBy: "quota" }}
        budget={BUDGET}
        canEdit
      />,
    );

    const note = screen.getByTestId("cpu-clamped").textContent ?? "";
    expect(note).toContain("8");
    expect(note).toContain("4");
    expect(note).toMatch(/額度/); // which limit bound, not just that one did
  });

  it("draws no budget half at all when this deploy caps nobody", () => {
    render(<ItemEnvironmentPanel env={IDLE} budget={null} canEdit />);

    expect(screen.queryByTestId("budget-gauge")).toBeNull();
    expect(screen.queryByTestId("cpu-input")).toBeNull();
    // …but the half that is about a machine rather than a budget stays.
    expect(screen.getByTestId("environment-status")).toBeTruthy();
  });

  it("leads with THIS item, with the person's total beside it", () => {
    render(
      <ItemEnvironmentPanel
        env={{ ...IDLE, running: true }}
        budget={BUDGET}
        canEdit
      />,
    );

    expect(screen.getByTestId("this-item-usage").textContent).toContain("2");
    expect(screen.getByTestId("budget-gauge")).toBeTruthy();
  });
});

describe("what is clickable", () => {
  it("offers close while it runs, and locks the size behind it", () => {
    render(
      <ItemEnvironmentPanel
        env={{ ...IDLE, running: true }}
        budget={BUDGET}
        canEdit
      />,
    );

    expect(screen.getByTestId("close-environment")).toBeTruthy();
    expect(screen.getByTestId("cpu-input").hasAttribute("disabled")).toBe(true);
  });

  it("opens the size for editing once nothing is running", () => {
    render(<ItemEnvironmentPanel env={IDLE} budget={BUDGET} canEdit />);

    expect(screen.getByTestId("cpu-input").hasAttribute("disabled")).toBe(false);
    expect(screen.queryByTestId("close-environment")).toBeNull();
  });

  it("is read-only for someone who may see it but not spend the owner's budget", () => {
    render(
      <ItemEnvironmentPanel env={IDLE} budget={BUDGET} canEdit={false} />,
    );

    expect(screen.getByTestId("cpu-input").hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("cpu-value")).toBeTruthy(); // still legible — that is the point
  });
});


describe("a deploy that will not honour the dial", () => {
  it("offers no dial, and says it cannot confirm rather than that it is off", () => {
    // #712 one layer up: billing what was REQUESTED instead of what is APPLIED
    // let an undeclared App hold a core for free. The same gap here is worse
    // because a PERSON chose the number — they set 2, the panel shows 2, and
    // the sandbox runs uncapped.
    //
    // "Cannot confirm" rather than "not enforced": `HttpSandbox` reports an
    // unreachable host identically to one that caps nothing, so a stronger
    // claim would be inventing a distinction the backend cannot make.
    render(
      <ItemEnvironmentPanel
        env={{ ...IDLE, enforcedCpuCores: null }}
        budget={BUDGET}
        canEdit
      />,
    );

    expect(screen.queryByTestId("cpu-input")).toBeNull();
    const note = screen.getByTestId("cpu-unenforced").textContent ?? "";
    expect(note).toMatch(/無法確認|can't confirm/);
  });

  it("still draws the dial where the backend does apply a ceiling", () => {
    render(<ItemEnvironmentPanel env={IDLE} budget={BUDGET} canEdit />);

    expect(screen.getByTestId("cpu-input")).toBeTruthy();
    expect(screen.queryByTestId("cpu-unenforced")).toBeNull();
  });
});
