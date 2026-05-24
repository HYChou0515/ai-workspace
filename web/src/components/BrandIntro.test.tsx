// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { BrandIntro } from "./BrandIntro";

describe("BrandIntro", () => {
  afterEach(cleanup);

  it("renders the brand lockup then dismisses on click", async () => {
    const { container } = render(<BrandIntro />);
    expect(container.querySelector(".brand-intro")).toBeInTheDocument();
    expect(screen.getByText("3.0")).toBeInTheDocument();
    expect(container.querySelector(".rca-mark-draw")).toBeInTheDocument(); // animated mark

    await userEvent.click(container.querySelector(".brand-intro")!);
    expect(container.querySelector(".brand-intro")).not.toBeInTheDocument();
  });

  it("auto-dismisses after its timers", async () => {
    const { container } = render(<BrandIntro />);
    // leaving → gone happens on timers (~1.15s + .45s); wait it out
    await waitFor(
      () => expect(container.querySelector(".brand-intro")).not.toBeInTheDocument(),
      { timeout: 3000 },
    );
    // keep act() happy if any trailing state flush remains
    await act(async () => {});
  });
});
