// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { LocaleProvider } from "../../lib/i18n";
import { QualityDetails } from "./QualityDetails";

afterEach(cleanup);

const wrap = (ui: React.ReactElement) => render(<LocaleProvider>{ui}</LocaleProvider>);

describe("QualityDetails (#460 P7)", () => {
  it("shows the score badge with a visible good/ok/bad verdict label", () => {
    wrap(<QualityDetails score={22} rationale="OCR soup, no structure." />);
    expect(screen.getByTestId("kb-quality-badge")).toHaveTextContent("22");
    const verdict = screen.getByTestId("kb-quality-verdict");
    expect((verdict.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("keeps the rationale collapsed until expanded, then shows the full text", async () => {
    const user = userEvent.setup();
    const long = "OCR soup, no structure. ".repeat(10).trim();
    wrap(<QualityDetails score={22} rationale={long} />);
    expect(screen.queryByTestId("kb-quality-panel")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("kb-quality-details-toggle"));
    expect(screen.getByTestId("kb-quality-rationale")).toHaveTextContent(long);
  });

  it("renders the per-dimension breakdown detail scores when expanded (#460 P8)", async () => {
    const user = userEvent.setup();
    wrap(<QualityDetails score={80} rationale="Solid." breakdown={{ accuracy: 9, coverage: 7 }} />);
    await user.click(screen.getByTestId("kb-quality-details-toggle"));
    const bd = screen.getByTestId("kb-quality-breakdown");
    expect(within(bd).getByTestId("kb-quality-dim-accuracy")).toHaveTextContent("accuracy");
    expect(within(bd).getByTestId("kb-quality-dim-accuracy")).toHaveTextContent("9");
    expect(within(bd).getByTestId("kb-quality-dim-coverage")).toHaveTextContent("7");
  });

  it("shows no breakdown block when there are no detail scores", async () => {
    const user = userEvent.setup();
    wrap(<QualityDetails score={80} rationale="Solid." />);
    await user.click(screen.getByTestId("kb-quality-details-toggle"));
    expect(screen.queryByTestId("kb-quality-breakdown")).not.toBeInTheDocument();
  });

  it("still expands (breakdown only) when there is no rationale", async () => {
    const user = userEvent.setup();
    wrap(<QualityDetails score={55} breakdown={{ depth: 5 }} />);
    await user.click(screen.getByTestId("kb-quality-details-toggle"));
    expect(screen.getByTestId("kb-quality-dim-depth")).toHaveTextContent("5");
    expect(screen.queryByTestId("kb-quality-rationale")).not.toBeInTheDocument();
  });
});
