// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../../test/queryWrapper";
import { EMPTY_FILTERS } from "../home.helpers";
import { HomeSidebar } from "./HomeSidebar";

const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, {
    wrapper: ({ children }) => (
      <QueryWrap>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryWrap>
    ),
  });

function setup(over: Partial<Parameters<typeof HomeSidebar>[0]> = {}) {
  const onOpenKnowledge = vi.fn();
  const onOpenChats = vi.fn();
  render(
    <HomeSidebar
      items={[]}
      currentUser="alice"
      activeTab="all"
      onTab={() => {}}
      pinned={new Set()}
      recent={[]}
      filters={EMPTY_FILTERS}
      onFilters={() => {}}
      onNewInvestigation={() => {}}
      onOpenTemplates={() => {}}
      onOpenInvestigation={() => {}}
      onOpenKnowledge={onOpenKnowledge}
      onOpenChats={onOpenChats}
      {...over}
    />,
  );
  return { onOpenKnowledge, onOpenChats };
}

describe("HomeSidebar — Knowledge base nav", () => {
  afterEach(cleanup);

  it("opens the KB collections surface from the Knowledge link", async () => {
    const { onOpenKnowledge } = setup();
    await userEvent.click(screen.getByRole("button", { name: /^Knowledge$/ }));
    expect(onOpenKnowledge).toHaveBeenCalledTimes(1);
  });

  it("opens the KB chats surface from the Chat link", async () => {
    const { onOpenChats } = setup();
    await userEvent.click(screen.getByRole("button", { name: /^Chat$/ }));
    expect(onOpenChats).toHaveBeenCalledTimes(1);
  });
});
