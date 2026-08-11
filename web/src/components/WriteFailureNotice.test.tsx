// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dismissWriteFailure, reportWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import { HttpError } from "../api/http";
import { WriteFailureNotice } from "./WriteFailureNotice";

describe("WriteFailureNotice", () => {
  beforeEach(() => resetWriteFailures());
  afterEach(cleanup);

  it("shows nothing until something fails — it is not a permanent chrome", () => {
    render(<WriteFailureNotice />);
    expect(screen.queryByTestId("write-failure")).toBeNull();
  });

  it("appears when a write fails, without the page having to re-render on its own", () => {
    render(<WriteFailureNotice />);
    act(() => reportWriteFailure(new HttpError(500, "500 Internal Server Error")));
    expect(screen.getByTestId("write-failure")).toBeInTheDocument();
  });

  // The whole reported symptom: a 403 that only F12 could see. The copy has to
  // name the cause, not echo a status line nobody reads as "you lack a verb".
  it("says a 403 is a permission problem, in words", () => {
    render(<WriteFailureNotice />);
    act(() => reportWriteFailure(new HttpError(403, "403 Forbidden: not permitted")));
    expect(screen.getByTestId("write-failure")).toHaveTextContent("權限");
  });

  it("announces itself to assistive tech — a silent banner is the same bug again", () => {
    render(<WriteFailureNotice />);
    act(() => reportWriteFailure(new HttpError(403, "nope")));
    expect(screen.getByTestId("write-failure")).toHaveAttribute("role", "alert");
  });

  it("can be dismissed", () => {
    render(<WriteFailureNotice />);
    act(() => reportWriteFailure(new HttpError(500, "boom")));
    fireEvent.click(screen.getByTestId("write-failure-dismiss"));
    expect(screen.queryByTestId("write-failure")).toBeNull();
  });

  it("clears when the store is cleared from anywhere else", () => {
    render(<WriteFailureNotice />);
    act(() => reportWriteFailure(new HttpError(500, "boom")));
    act(() => dismissWriteFailure());
    expect(screen.queryByTestId("write-failure")).toBeNull();
  });

  // Keeps the detail available without making it the headline — the user needs
  // to know it did not save; a developer needs to know what the server said.
  it("keeps the server's own message visible as detail", () => {
    render(<WriteFailureNotice />);
    act(() => reportWriteFailure(new HttpError(507, "507: workspace full")));
    expect(screen.getByTestId("write-failure")).toHaveTextContent("workspace full");
  });
});
