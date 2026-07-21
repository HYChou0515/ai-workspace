// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AccessChip } from "./AccessChip";
import { ITEM_VISIBILITY_HINT, ITEM_VISIBILITY_LABEL } from "../lib/itemPermission";

afterEach(cleanup);

describe("#578 AccessChip", () => {
  it("describes access without claiming the viewer is the owner", () => {
    // The chip renders on EVERY row, including other people's items and — for a
    // superuser — items nobody shared with them. Second-person copy ("Only you")
    // is simply false there, in both directions.
    for (const hint of Object.values(ITEM_VISIBILITY_HINT)) {
      expect(hint).not.toMatch(/\byou\b/i);
    }
  });

  it("makes the most-open state the loud one", () => {
    // The feature exists to answer "what have I left open?". If `restricted`
    // shouts and `public` whispers, the scan surfaces the wrong rows.
    render(
      <>
        <AccessChip visibility="public" />
        <AccessChip visibility="restricted" />
      </>,
    );
    const pub = screen.getByText("Public");
    const res = screen.getByText("Restricted");
    // warn is this codebase's caution tone (severityTone P2, statusTone triaging).
    expect(pub.style.color).toBe("var(--warn)");
    expect(res.style.color).not.toBe("var(--warn)");
  });

  it("says it cannot tell, rather than guessing Public, when the setting is unreadable", () => {
    render(<AccessChip visibility="unknown" />);
    expect(screen.queryByText("Public")).not.toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shares one label table with the share dialog", () => {
    expect(ITEM_VISIBILITY_LABEL.public).toBe("Public");
    expect(ITEM_VISIBILITY_LABEL.restricted).toBe("Restricted");
    expect(ITEM_VISIBILITY_LABEL.private).toBe("Private");
  });
});
