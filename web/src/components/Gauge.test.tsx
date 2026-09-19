// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Gauge, Meter } from "./Gauge";

afterEach(cleanup);

describe("Meter", () => {
  it("is a progressbar whose fill is the used fraction, capped at 100", () => {
    const { rerender } = render(<Meter used={3} limit={6} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(bar.firstElementChild).toHaveStyle({ width: "50%" });
    // Over the limit is a real state (a quota lowered under a running
    // sandbox); the bar must not grow past its track.
    rerender(<Meter used={9} limit={6} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("draws nothing for an unlimited dimension — there is nothing to be a fraction of", () => {
    render(<Meter used={3} limit={0} />);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});

describe("Gauge", () => {
  it("names the dimension, shows used / limit, and a meter", () => {
    render(<Gauge label="CPU" used={1} limit={6} format={String} />);
    expect(screen.getByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("1 / 6")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("still shows usage without a limit, with no denominator, no bar, and says so", () => {
    render(<Gauge label="Memory" used={512} limit={0} format={(n) => `${n} MB`} />);
    expect(screen.getByText("512 MB")).toBeInTheDocument();
    expect(screen.queryByText(/\//)).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText(/no limit|無上限|unlimited/i)).toBeInTheDocument();
  });
});
