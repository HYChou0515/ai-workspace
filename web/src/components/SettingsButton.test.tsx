// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SettingsButton } from "./SettingsButton";

describe("SettingsButton", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("opens a settings dialog and switches the theme", async () => {
    render(<SettingsButton />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /settings/i }));
    const dialog = screen.getByRole("dialog", { name: /settings/i });
    expect(dialog).toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("rca:theme")).toBe("dark");
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");

    await userEvent.click(screen.getByRole("radio", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
