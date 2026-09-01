// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ShareTabs } from "./ShareTabs";

afterEach(cleanup);

describe("ShareTabs", () => {
  it("names each side and how many grants it already holds, so the other tab is never a blind spot", () => {
    render(
      <ShareTabs
        value="people"
        onChange={vi.fn()}
        tabs={[
          { id: "people", label: "People", count: 8 },
          { id: "groups", label: "Groups", count: 2 },
        ]}
      />,
    );
    expect(screen.getByRole("tab", { name: /People/ })).toHaveTextContent("8");
    expect(screen.getByRole("tab", { name: /Groups/ })).toHaveTextContent("2");
    expect(screen.getByRole("tab", { name: /People/ })).toHaveAttribute("aria-selected", "true");
  });

  it("hands the caller the tab that was picked", () => {
    const onChange = vi.fn();
    render(
      <ShareTabs
        value="people"
        onChange={onChange}
        tabs={[
          { id: "people", label: "People", count: 0 },
          { id: "groups", label: "Groups", count: 0 },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Groups/ }));
    expect(onChange).toHaveBeenCalledWith("groups");
  });
});
