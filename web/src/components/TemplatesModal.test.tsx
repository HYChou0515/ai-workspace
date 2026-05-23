// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TemplatesModal } from "./TemplatesModal";

afterEach(cleanup);

describe("<TemplatesModal />", () => {
  it("lists the available templates and emits the picked one", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    render(
      <TemplatesModal
        open
        templates={["default", "methodology", "smt-reflow-example"]}
        onPick={onPick}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("methodology")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /smt-reflow-example/i }));
    expect(onPick).toHaveBeenCalledWith("smt-reflow-example");
  });

  it("does not render when closed", () => {
    render(<TemplatesModal open={false} templates={["default"]} onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.queryByText("default")).toBeNull();
  });
});
