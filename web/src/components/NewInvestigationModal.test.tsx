// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { NewInvestigationModal } from "./NewInvestigationModal";

// The modal reads templates through useTemplates (TanStack Query), so every
// render needs a QueryClient in scope.
const render = (ui: ReactElement) => rtlRender(ui, { wrapper: QueryWrap });

afterEach(cleanup);

describe("<NewInvestigationModal />", () => {
  it("disables the submit button until title is filled", async () => {
    const user = userEvent.setup();
    render(<NewInvestigationModal open onSubmit={vi.fn()} onClose={vi.fn()} />);
    const submit = screen.getByRole("button", { name: /create & ask agent/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText(/title/i), "Reflow drift");
    expect(submit).not.toBeDisabled();
  });

  it("emits the form payload on submit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<NewInvestigationModal open onSubmit={onSubmit} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(/title/i), "Reflow drift");
    await user.type(
      screen.getByLabelText(/description/i),
      "AOI flagged bridging on lot 25-W14",
    );
    await user.type(screen.getByLabelText(/product/i), "MX-7 board");
    await user.click(screen.getByRole("button", { name: /create & ask agent/i }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toEqual({
      title: "Reflow drift",
      description: "AOI flagged bridging on lot 25-W14",
      severity: "P2",
      product: "MX-7 board",
      topics: [],
      templateProfile: "default",
    });
  });

  it("accepts topic chips on Enter and dedupes them", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<NewInvestigationModal open onSubmit={onSubmit} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(/title/i), "x");
    const topics = screen.getByLabelText(/topics/i);
    await user.type(topics, "SMT 1{Enter}");
    await user.type(topics, "Reflow zone-3{Enter}");
    await user.type(topics, "SMT 1{Enter}"); // duplicate

    await user.click(screen.getByRole("button", { name: /create & ask agent/i }));
    expect(onSubmit.mock.calls[0]?.[0]?.topics).toEqual(["SMT 1", "Reflow zone-3"]);
  });

  it("supports selecting a severity via segmented control", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<NewInvestigationModal open onSubmit={onSubmit} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(/title/i), "x");
    await user.click(screen.getByText("P0"));
    await user.click(screen.getByRole("button", { name: /create & ask agent/i }));
    expect(onSubmit.mock.calls[0]?.[0]?.severity).toBe("P0");
  });

  it("does not render when `open` is false", () => {
    render(<NewInvestigationModal open={false} onSubmit={vi.fn()} onClose={vi.fn()} />);
    expect(screen.queryByLabelText(/title/i)).toBeNull();
  });

  it("calls onClose when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<NewInvestigationModal open onSubmit={vi.fn()} onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("edit mode: prefills, hides the template picker, saves changes", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <NewInvestigationModal
        open
        mode="edit"
        initialValues={{
          title: "Old title",
          description: "old brief",
          severity: "P1",
          product: "MX-7 board",
          topics: ["reflow"],
        }}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/title/i)).toHaveValue("Old title"); // prefilled
    expect(screen.getByText("reflow")).toBeInTheDocument(); // topic chip
    expect(screen.queryByLabelText(/template/i)).toBeNull(); // no template on edit

    const title = screen.getByLabelText(/title/i);
    await user.clear(title);
    await user.type(title, "New title");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "New title",
        severity: "P1",
        product: "MX-7 board",
        topics: ["reflow"],
      }),
    );
  });
});
