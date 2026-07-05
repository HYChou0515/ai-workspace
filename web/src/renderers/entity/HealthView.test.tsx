// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityHealthFinding } from "../../api/entities";
import { HealthView } from "./HealthView";

afterEach(cleanup);

const err = (number: number, message: string, type_name = "issue"): EntityHealthFinding => ({
  type_name,
  number,
  level: "error",
  message,
});
const warn = (number: number, message: string, field: string, type_name = "issue"): EntityHealthFinding => ({
  type_name,
  number,
  level: "warning",
  message,
  field,
});

describe("HealthView filtering (§F)", () => {
  it("filters findings by level", () => {
    render(<HealthView findings={[err(2, "boom"), warn(3, "lint", "status")]} />);
    fireEvent.change(screen.getByLabelText("filter level"), { target: { value: "error" } });
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByText(/lint/)).not.toBeInTheDocument();
  });

  it("filters findings by entity type", () => {
    render(<HealthView findings={[err(2, "boom", "issue"), err(9, "nope", "milestone")]} />);
    fireEvent.change(screen.getByLabelText("filter type"), { target: { value: "milestone" } });
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
    expect(screen.getByText("nope")).toBeInTheDocument();
  });

  it("filters findings by field", () => {
    render(<HealthView findings={[warn(2, "s off", "status"), warn(3, "d off", "due")]} />);
    fireEvent.change(screen.getByLabelText("filter field"), { target: { value: "due" } });
    expect(screen.queryByText(/s off/)).not.toBeInTheDocument();
    expect(screen.getByText(/d off/)).toBeInTheDocument();
  });

  it("shows a filtered-empty note when filters exclude everything", () => {
    render(<HealthView findings={[err(2, "boom")]} />);
    fireEvent.change(screen.getByLabelText("filter level"), { target: { value: "warning" } });
    expect(screen.getByText(/no findings match/i)).toBeInTheDocument();
  });
});

describe("HealthView jump (§F)", () => {
  it("calls onJump with the finding when a finding is activated", () => {
    const onJump = vi.fn();
    const f = err(2, "boom");
    render(<HealthView findings={[f]} onJump={onJump} />);
    fireEvent.click(screen.getByRole("button", { name: /issue #2/ }));
    expect(onJump).toHaveBeenCalledWith(f);
  });

  it("renders findings as plain rows (not buttons) when no onJump is given", () => {
    render(<HealthView findings={[err(2, "boom")]} />);
    expect(screen.queryByRole("button", { name: /issue #2/ })).not.toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
