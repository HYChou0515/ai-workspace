// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RetrievalToggles, WikiBadge } from "./RetrievalToggles";

describe("RetrievalToggles", () => {
  afterEach(cleanup);

  it("reports turning the wiki on (keeping document search)", async () => {
    const onChange = vi.fn();
    render(<RetrievalToggles docSearch wiki={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch", { name: "Knowledge wiki" }));
    expect(onChange).toHaveBeenCalledWith({ docSearch: true, wiki: true });
  });

  it("reflects each mode's state through aria-checked", () => {
    render(<RetrievalToggles docSearch={false} wiki onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: "Document search" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("switch", { name: "Knowledge wiki" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("shows the 'both' hint only when both modes are on", () => {
    const { rerender } = render(<RetrievalToggles docSearch wiki={false} onChange={() => {}} />);
    expect(screen.queryByText(/draw on both/i)).not.toBeInTheDocument();
    rerender(<RetrievalToggles docSearch wiki onChange={() => {}} />);
    expect(screen.getByText(/draw on both/i)).toBeInTheDocument();
  });
});

describe("WikiBadge", () => {
  afterEach(cleanup);
  it("renders the Wiki label", () => {
    render(<WikiBadge />);
    expect(screen.getByText("Wiki")).toBeInTheDocument();
  });
});
