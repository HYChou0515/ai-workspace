// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ModalActions } from "./ModalActions";

afterEach(cleanup);

describe("ModalActions", () => {
  it("pins itself to the bottom of the scrolling panel so Save never scrolls away", () => {
    render(
      <ModalActions>
        <button type="button">Save</button>
      </ModalActions>,
    );
    const bar = screen.getByTestId("modal-actions");
    expect(bar.style.position).toBe("sticky");
    expect(bar.style.bottom).not.toBe("");
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });
});
