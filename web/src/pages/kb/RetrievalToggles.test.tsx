// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RetrievalToggles, WikiBadge } from "./RetrievalToggles";

describe("RetrievalToggles", () => {
  afterEach(cleanup);

  // #171: de-jargoned, zh-TW default (no LocaleProvider): 文件搜尋 / 知識百科.
  it("reports turning the wiki on (keeping document search)", async () => {
    const onChange = vi.fn();
    render(<RetrievalToggles docSearch wiki={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch", { name: "知識百科" }));
    expect(onChange).toHaveBeenCalledWith({ docSearch: true, wiki: true });
  });

  it("reflects each mode's state through aria-checked", () => {
    render(<RetrievalToggles docSearch={false} wiki onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: "文件搜尋" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("switch", { name: "知識百科" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("shows the 'both' hint only when both modes are on", () => {
    const { rerender } = render(<RetrievalToggles docSearch wiki={false} onChange={() => {}} />);
    expect(screen.queryByText(/兩者都會用/)).not.toBeInTheDocument();
    rerender(<RetrievalToggles docSearch wiki onChange={() => {}} />);
    expect(screen.getByText(/兩者都會用/)).toBeInTheDocument();
  });
});

describe("WikiBadge", () => {
  afterEach(cleanup);
  it("renders the Wiki label", () => {
    render(<WikiBadge />);
    expect(screen.getByText("Wiki")).toBeInTheDocument();
  });
});
