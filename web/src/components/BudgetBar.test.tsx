// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LlmBudget } from "../api/types";
import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { BudgetBar } from "./BudgetBar";

// docs/plan-llm-budget.md P3: what the deploy's class answered, above the
// composer — remaining first, both labelled (「剩 $11／共 $20」), then the
// deploy's message and link, all on ONE line of its own in the status row (as a
// cell it wrapped in the narrow item chat and began its line with a dangling
// 「·」 — found in the demo); a tone said in words as well as colour; muted
// 「預算暫時查不到」 when it could not answer; nothing when there is nothing.
const word = (key: string, vars?: Record<string, string | number>) => translate("zh-TW", key as never, vars);

function mount(answer: LlmBudget) {
  const client = { getLlmBudget: vi.fn(async () => answer) };
  const { container } = renderWithQuery(
    <div className="composer-status" data-testid="row">
      <BudgetBar client={client} />
    </div>,
  );
  return { client, container };
}

afterEach(cleanup);

describe("BudgetBar", () => {
  it("draws nothing when the deploy configured no budget", async () => {
    const { client } = mount({ status: "off", budget: null });
    await vi.waitFor(() => expect(client.getLlmBudget).toHaveBeenCalled());
    expect(screen.getByTestId("row")).toBeEmptyDOMElement();
  });

  it("draws nothing when the class had nothing to show", async () => {
    const { client } = mount({ status: "ok", budget: null });
    await vi.waitFor(() => expect(client.getLlmBudget).toHaveBeenCalled());
    expect(screen.getByTestId("row")).toBeEmptyDOMElement();
  });

  it("says what is left first, then the total, both labelled", async () => {
    mount({
      status: "ok",
      budget: { remaining: 11, total: 20, currency: "USD", message: "", link: null, tone: "info" },
    });
    const cell = await screen.findByTestId("llm-budget");
    expect(cell).toHaveTextContent(word("budget.remainingOfTotal", { remaining: "$11", total: "$20" }));
    expect(cell.textContent?.indexOf("$11")).toBeLessThan(cell.textContent?.indexOf("$20") ?? -1);
    // A line of its own: no separator dot before it, wherever the row wraps.
    expect(cell).toHaveAttribute("data-status-line");
  });

  it("keeps cents only when there are any", async () => {
    mount({
      status: "ok",
      budget: { remaining: 3.5, total: 20, currency: "USD", message: "", link: null, tone: "info" },
    });
    expect(await screen.findByTestId("llm-budget")).toHaveTextContent("$3.50");
  });

  it("shows the deploy's message and link after the figure, on the same line; the link opens elsewhere", async () => {
    mount({
      status: "ok",
      budget: {
        remaining: 11,
        total: 20,
        currency: "USD",
        message: "Resets on the 1st.",
        link: { label: "Ask for more", url: "https://budget.corp/apply" },
        tone: "info",
      },
    });
    const line = await screen.findByTestId("llm-budget");
    expect(line).toHaveAttribute("data-status-line");
    expect(line.textContent?.indexOf("$20")).toBeLessThan(line.textContent?.indexOf("Resets on the 1st.") ?? -1);
    const link = within(line).getByRole("link", { name: "Ask for more" });
    expect(link).toHaveAttribute("href", "https://budget.corp/apply");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("a message alone is shown without numbers", async () => {
    mount({
      status: "ok",
      budget: { remaining: null, total: null, currency: "USD", message: "Budget is pooled per team.", link: null, tone: "info" },
    });
    const line = await screen.findByTestId("llm-budget");
    expect(line).toHaveTextContent("Budget is pooled per team.");
    expect(line).not.toHaveTextContent("AI");
  });

  it("never renders the message as HTML", async () => {
    mount({
      status: "ok",
      budget: { remaining: null, total: null, currency: "USD", message: "<b>bold</b>", link: null, tone: "info" },
    });
    const line = await screen.findByTestId("llm-budget");
    expect(line).toHaveTextContent("<b>bold</b>");
    expect(line.querySelector("b")).toBeNull();
  });

  it("says a warning in words, not colour alone", async () => {
    mount({
      status: "ok",
      budget: { remaining: 1, total: 20, currency: "USD", message: "", link: null, tone: "critical" },
    });
    const cell = await screen.findByTestId("llm-budget");
    expect(cell).toHaveTextContent(word("budget.tone.critical"));
    expect(cell).toHaveAttribute("data-tone", "critical");
  });

  it("says, muted, that the budget could not be read", async () => {
    mount({ status: "unavailable", budget: null });
    const line = await screen.findByTestId("llm-budget");
    expect(line).toHaveTextContent(word("budget.unavailable"));
    expect(line).toHaveAttribute("data-status-line");
  });
});
