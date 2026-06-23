// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Skeleton } from "./Skeleton";

describe("Skeleton", () => {
  afterEach(cleanup);

  it("is decorative — hidden from assistive tech so a loading placeholder is never announced as content", () => {
    const { container } = render(<Skeleton />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });

  it("forwards a className so each caller can size its own placeholder", () => {
    const { container } = render(<Skeleton className="kb-skel--row" />);
    expect(container.firstElementChild).toHaveClass("skeleton");
    expect(container.firstElementChild).toHaveClass("kb-skel--row");
  });
});
