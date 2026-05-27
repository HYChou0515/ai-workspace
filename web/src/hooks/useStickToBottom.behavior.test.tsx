// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useStickToBottom } from "./useStickToBottom";

function Harness({ step }: { step: number }) {
  const ref = useStickToBottom<HTMLDivElement>(step);
  return <div data-testid="box" ref={ref} style={{ height: 100, overflow: "auto" }} />;
}

/** happy-dom has no layout, so fake the scroll metrics the hook reads. */
function fakeMetrics(el: HTMLElement, scrollHeight: number, clientHeight: number) {
  Object.defineProperty(el, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: clientHeight, configurable: true });
}

describe("useStickToBottom behavior", () => {
  afterEach(cleanup);

  it("pins to the bottom as content grows", () => {
    const { getByTestId, rerender } = render(<Harness step={1} />);
    const box = getByTestId("box");
    fakeMetrics(box, 1000, 100);
    rerender(<Harness step={2} />); // content grew → pin
    expect(box.scrollTop).toBe(1000);
  });

  it("stops auto-scrolling once the user wheels up, even mid-stream", () => {
    const { getByTestId, rerender } = render(<Harness step={1} />);
    const box = getByTestId("box");
    fakeMetrics(box, 1000, 100);
    rerender(<Harness step={2} />);
    expect(box.scrollTop).toBe(1000);

    // user scrolls up; the wheel intent must release immediately so the next
    // streamed chunk does NOT yank back to the bottom.
    box.scrollTop = 200;
    box.dispatchEvent(new WheelEvent("wheel", { deltaY: -50, bubbles: true }));
    rerender(<Harness step={3} />); // a chunk arrives
    expect(box.scrollTop).toBe(200); // stayed where the user left it
  });

  it("resumes auto-scrolling once the user returns to the bottom", () => {
    const { getByTestId, rerender } = render(<Harness step={1} />);
    const box = getByTestId("box");
    fakeMetrics(box, 1000, 100);
    rerender(<Harness step={2} />);

    box.scrollTop = 200;
    box.dispatchEvent(new WheelEvent("wheel", { deltaY: -50, bubbles: true }));
    rerender(<Harness step={3} />);
    expect(box.scrollTop).toBe(200);

    // back to the bottom → re-stick → following resumes
    box.scrollTop = 990;
    box.dispatchEvent(new Event("scroll"));
    rerender(<Harness step={4} />);
    expect(box.scrollTop).toBe(1000);
  });
});
