// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchResult } from "../../api/types";
import { SearchPanel } from "./SearchPanel";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";

afterEach(cleanup);

function stub() {
  return {
    searchFiles: vi.fn(async (): Promise<SearchResult[]> => []),
    replaceInFiles: vi.fn(async (): Promise<number> => 0),
  };
}

function renderPanel() {
  return render(
    <WorkspaceSlugProvider value="rca">
      <SearchPanel investigationId="inv1" onOpenFile={vi.fn()} client={stub()} />
    </WorkspaceSlugProvider>,
  );
}

describe("<SearchPanel /> layout (#460 P2)", () => {
  it("clips its own overflow so it never paints over the editor pane", () => {
    renderPanel();
    expect(screen.getByTestId("search-frame").style.overflow).toBe("hidden");
  });

  it("keeps the match/word/regex toggles from being squeezed out (flex-shrink 0)", () => {
    renderPanel();
    expect(screen.getByTestId("search-toggles").style.flexShrink).toBe("0");
  });

  it("lets the search field row shrink instead of pushing the toggles out (min-width 0)", () => {
    renderPanel();
    expect(screen.getByTestId("search-field").style.minWidth).toBe("0");
  });
});
