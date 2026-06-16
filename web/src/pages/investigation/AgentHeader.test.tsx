// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { renderWithQuery } from "../../test/queryWrapper";
import { AgentHeader } from "./AgentPanel";

describe("AgentHeader export", () => {
  afterEach(cleanup);

  it("Export downloads the re-ingestable .chat.json, not the debug dump", () => {
    // Round-trip contract (issue #39): the header's Export must produce
    // a file the KB upload path can ingest directly. The full debug
    // dump (`/export`) stays curl-only.
    //
    // Query provider + router: the header hosts the HealthDot (#51 P5),
    // which reads /health/checks through TanStack Query and links to
    // /diagnostics.
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: /export/i });
    expect(link.getAttribute("href")).toContain("/investigations/inv-1/export-chat");
  });
});
