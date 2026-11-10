// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LlmBudget } from "../api/types";
import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { BudgetBar, formatMoney } from "./BudgetBar";

// docs/plan-llm-budget.md P3: what the deploy's class answered, above the
// composer — remaining first, both labelled (「剩 $11／共 $20」), then the
// deploy's message and link, as ONE cell of the status row beside the other
// gauges — it moves to a new line only when the row has no room (the user:
// 「擺不下才換行」); a tone said in words as well as colour; muted
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
    // A cell beside the others, not a forced line of its own.
    expect(cell).not.toHaveAttribute("data-status-line");
    expect(cell.parentElement).toBe(screen.getByTestId("row"));
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
    expect(line).not.toHaveAttribute("data-status-line");
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
    expect(line).not.toHaveAttribute("data-status-line");
  });
});

// Review round 1 (#1015): with `narrowSymbol`, a TWD deploy showed `$11` — the
// same as USD. One set of symbols for every reader, unambiguous across codes.
describe("formatMoney", () => {
  it("names each currency unambiguously, the dollar as $ (the user's example)", () => {
    expect(formatMoney(11, "USD")).toBe("$11");
    expect(formatMoney(11, "TWD")).toBe("NT$11");
    expect(formatMoney(11, "EUR")).toBe("€11");
    expect(formatMoney(3.5, "USD")).toBe("$3.50");
  });

  it("says an unknown code as given, and a malformed one after a rounded number", () => {
    expect(formatMoney(11, "XYZ")).toBe("XYZ\u00a011"); // Intl keeps code and number together
    expect(formatMoney(0.1 + 0.2, "credits")).toBe("0.30 credits"); // rounded, cents as with $3.50
    expect(formatMoney(11, "")).toBe("11");
  });
});
