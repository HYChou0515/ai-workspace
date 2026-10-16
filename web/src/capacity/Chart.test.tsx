// @vitest-environment happy-dom
/**
 * A chart draws through the lazily loaded ECharts module (D15): initialised once
 * the module arrives, redrawn when its option changes, and released when it
 * leaves the page.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ init: 0, options: [] as unknown[], disposed: 0 }));

vi.mock("./echarts", () => ({
  echarts: {
    init: () => {
      calls.init += 1;
      return {
        setOption: (o: unknown) => calls.options.push(o),
        resize: () => {},
        dispose: () => {
          calls.disposed += 1;
        },
      };
    },
  },
}));

import { Chart } from "./Chart";

afterEach(cleanup);

describe("Chart", () => {
  it("draws once ECharts arrives, redraws on a new option, and lets go on unmount", async () => {
    const first = { series: [{ type: "bar", data: [1] }] };
    const { rerender, unmount } = render(<Chart option={first} label="use" />);

    expect(screen.getByRole("img", { name: "use" })).toBeInTheDocument();
    await waitFor(() => expect(calls.init).toBe(1));
    expect(calls.options).toEqual([first]);

    const second = { series: [{ type: "bar", data: [2] }] };
    rerender(<Chart option={second} label="use" />);
    await waitFor(() => expect(calls.options).toEqual([first, second]));

    unmount();
    expect(calls.disposed).toBe(1);
  });
});
