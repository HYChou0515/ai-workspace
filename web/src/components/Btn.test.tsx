// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Btn } from "./Btn";

afterEach(cleanup);

/**
 * Btn drives its hover / disabled affordance from the shared `.btn` CSS class
 * (base.css) rather than inline styles, because inline styles can't express
 * :hover (#445). These assert the CSS hooks — class + data-variant/size/active
 * — are present so the stylesheet can target them.
 */
describe("Btn", () => {
  it("renders the shared .btn class with variant + size hooks", () => {
    render(
      <Btn variant="primary" size="sm">
        Go
      </Btn>,
    );
    const btn = screen.getByRole("button", { name: "Go" });
    expect(btn).toHaveClass("btn");
    expect(btn).toHaveAttribute("data-variant", "primary");
    expect(btn).toHaveAttribute("data-size", "sm");
  });

  it("defaults to secondary / md", () => {
    render(<Btn>Default</Btn>);
    const btn = screen.getByRole("button", { name: "Default" });
    expect(btn).toHaveAttribute("data-variant", "secondary");
    expect(btn).toHaveAttribute("data-size", "md");
  });

  it("marks the active (selected) state via data-active for the stylesheet", () => {
    render(<Btn active>Sel</Btn>);
    expect(screen.getByRole("button", { name: "Sel" })).toHaveAttribute("data-active");
  });

  it("disables the native button and blocks onClick when disabled", () => {
    const onClick = vi.fn();
    render(
      <Btn disabled onClick={onClick}>
        Nope
      </Btn>,
    );
    const btn = screen.getByRole("button", { name: "Nope" });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires onClick when enabled", () => {
    const onClick = vi.fn();
    render(<Btn onClick={onClick}>Click</Btn>);
    fireEvent.click(screen.getByRole("button", { name: "Click" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
