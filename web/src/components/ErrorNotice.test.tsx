// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { translate } from "../lib/i18n";
import { ErrorNotice } from "./ErrorNotice";

const zh = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const RAW = "RateLimitError: litellm.RateLimitError: slow down";

describe("ErrorNotice (docs/plan-friendly-errors.md D1)", () => {
  afterEach(cleanup);

  it("says what happened and what to do — not the provider's string", () => {
    render(<ErrorNotice failure={{ kind: "rate_limited", params: { waited_s: 120 }, raw: RAW }} />);

    const notice = screen.getByTestId("error-notice");
    expect(notice).toHaveTextContent(zh("failure.rate_limited.waited", { waited_s: 120 }));
    expect(notice).toHaveTextContent(zh("failure.rate_limited.do"));
  });

  it("keeps the raw text for whoever debugs it, collapsed until asked for", () => {
    render(<ErrorNotice failure={{ kind: "rate_limited", raw: RAW }} />);

    const details = screen.getByTestId("error-notice-details");
    expect(details.tagName).toBe("DETAILS");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent(zh("failure.details"));
    expect(details).toHaveTextContent(RAW);
  });

  it("copies the raw text exactly, so a report carries what was really said", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<ErrorNotice failure={{ kind: "timeout", raw: RAW }} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: zh("failure.copy") }));
    });

    expect(writeText).toHaveBeenCalledWith(RAW);
    expect(screen.getByRole("button", { name: zh("failure.copied") })).toBeInTheDocument();
  });

  it("words an old row's `error` as unknown, with its text under details", () => {
    render(<ErrorNotice failure={{ kind: "error", raw: "giving up after 3 attempts: Boom" }} />);

    expect(screen.getByTestId("error-notice")).toHaveTextContent(zh("failure.unknown.what"));
    expect(screen.getByTestId("error-notice-details")).toHaveTextContent("giving up after 3 attempts");
  });

  it("has no details to open when there is nothing technical to show", () => {
    render(<ErrorNotice failure={{ kind: "quota", headline: "工作區已滿", raw: "" }} />);

    expect(screen.getByTestId("error-notice")).toHaveTextContent("工作區已滿");
    expect(screen.queryByTestId("error-notice-details")).toBeNull();
  });

  it("announces a live failure to assistive tech, and only a live one", () => {
    const { rerender } = render(<ErrorNotice failure={{ kind: "auth", raw: RAW }} live />);
    expect(screen.getByTestId("error-notice")).toHaveAttribute("role", "alert");

    rerender(<ErrorNotice failure={{ kind: "auth", raw: RAW }} />);
    expect(screen.getByTestId("error-notice")).not.toHaveAttribute("role");
  });
});
