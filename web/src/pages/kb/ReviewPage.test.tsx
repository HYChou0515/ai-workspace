// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";

import { kbApi, type KbReviewInbox } from "../../api/kb";
import { BreadcrumbProvider } from "../../hooks/breadcrumbs";
import { QueryWrap } from "../../test/queryWrapper";
import { ReviewPage } from "./ReviewPage";

const inbox = (over: Partial<KbReviewInbox> = {}): KbReviewInbox => ({
  cards: [],
  questions: [],
  clusters: [],
  suppressed: [],
  total: 120, // > one page, so the pager's Next is enabled
  total_actionable: 0,
  ...over,
});

const renderPage = (ui: ReactElement) =>
  render(
    <MemoryRouter>
      <BreadcrumbProvider>{ui}</BreadcrumbProvider>
    </MemoryRouter>,
    { wrapper: QueryWrap },
  );

describe("ReviewPage (#506 G2 pagination)", () => {
  beforeEach(() => {
    vi.spyOn(kbApi, "getReviewInbox").mockResolvedValue(inbox());
    vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads the first page with a server-side limit + offset", async () => {
    renderPage(<ReviewPage />);
    await waitFor(() =>
      expect(kbApi.getReviewInbox).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 50, offset: 0, resolved: false, grouped: false }),
      ),
    );
  });

  it("advances the server offset when Next is clicked", async () => {
    renderPage(<ReviewPage />);
    const next = await screen.findByRole("button", { name: /next|下一頁/i });
    fireEvent.click(next);
    await waitFor(() =>
      expect(kbApi.getReviewInbox).toHaveBeenCalledWith(expect.objectContaining({ offset: 50 })),
    );
  });

  it("queries the grouped stream on the by-concept tab", async () => {
    renderPage(<ReviewPage />);
    fireEvent.click(await screen.findByRole("tab", { name: /by concept|依概念/i }));
    await waitFor(() =>
      expect(kbApi.getReviewInbox).toHaveBeenCalledWith(
        expect.objectContaining({ grouped: true, offset: 0 }),
      ),
    );
  });

  it("narrows to actionable rows server-side", async () => {
    renderPage(<ReviewPage />);
    fireEvent.click(await screen.findByLabelText(/only what i can act on|只看我能操作/i));
    await waitFor(() =>
      expect(kbApi.getReviewInbox).toHaveBeenCalledWith(
        expect.objectContaining({ actionable: true }),
      ),
    );
  });
});
