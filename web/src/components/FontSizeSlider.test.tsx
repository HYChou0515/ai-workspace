// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { FontScaleProvider } from "../hooks/fontScale";
import { FontSizeSlider } from "./FontSizeSlider";

function renderSlider() {
  render(
    <FontScaleProvider>
      <FontSizeSlider />
    </FontScaleProvider>,
  );
}

describe("FontSizeSlider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.fontSize = "";
  });
  afterEach(cleanup);

  it("shows the current scale as a percentage, defaulting to 100%", () => {
    renderSlider();
    const slider = screen.getByRole("slider", { name: "字體大小" });
    expect(slider).toHaveValue("100");
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("scales the document root live and persists when the slider moves", () => {
    renderSlider();
    const slider = screen.getByRole("slider", { name: "字體大小" });
    fireEvent.change(slider, { target: { value: "120" } });

    expect(screen.getByText("120%")).toBeInTheDocument();
    expect(document.documentElement.style.fontSize).toBe("120%");
    expect(localStorage.getItem("ui:font-scale")).toBe("1.2");
  });

  it("offers a reset that returns to 100% and is disabled there", () => {
    renderSlider();
    const reset = screen.getByRole("button", { name: "重置為預設大小" });
    expect(reset).toBeDisabled();

    fireEvent.change(screen.getByRole("slider", { name: "字體大小" }), {
      target: { value: "135" },
    });
    expect(reset).toBeEnabled();

    fireEvent.click(reset);
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(document.documentElement.style.fontSize).toBe("100%");
  });
});
