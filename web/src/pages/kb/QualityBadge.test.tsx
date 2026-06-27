// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

import { LocaleProvider } from "../../lib/i18n";

import { QualityBadge } from "./QualityBadge";

function renderBadge(score?: number | null) {
  return render(
    <LocaleProvider>
      <QualityBadge score={score} />
    </LocaleProvider>,
  );
}

describe("QualityBadge", () => {
  it("shows the score and a tone class for a scored doc", () => {
    renderBadge(32);
    const badge = screen.getByTestId("kb-quality-badge");
    expect(badge).toHaveTextContent("32");
    expect(badge.className).toContain("kb-quality--bad");
    expect(badge.getAttribute("title")).toContain("32");
  });

  it("renders nothing for an un-scored doc (neutral)", () => {
    const { container } = renderBadge(null);
    expect(container).toBeEmptyDOMElement();
  });
});
