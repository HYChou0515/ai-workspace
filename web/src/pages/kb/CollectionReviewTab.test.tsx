// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetKbMock, _seedDocQuestionMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { CollectionReviewTab } from "./CollectionReviewTab";

const renderTab = () =>
  render(<CollectionReviewTab collectionId="col-1" client={mockKbApi} />, { wrapper: QueryWrap });

/** Seed a finalized run (the mock completes generation synchronously). */
async function seedRun() {
  await mockKbApi.uploadDocument("col-1", new File(["RZ3 is the third zone"], "a.md"));
  return mockKbApi.generateContextCards("col-1", ["col-1/me/a.md"]);
}

describe("<CollectionReviewTab /> (#415)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("shows an empty state when nothing is awaiting review", async () => {
    renderTab();
    expect(await screen.findByText("目前沒有待審核項目。")).toBeInTheDocument();
  });

  it("lists a finalized run as a reviewable item", async () => {
    await seedRun();
    renderTab();
    expect(await screen.findByTestId("review-run")).toHaveTextContent("1 張卡片提案");
  });

  it("expands a run, accepts a proposal, commits it, and drops it from the queue", async () => {
    await seedRun();
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole("button", { name: /張卡片提案/ }));
    await user.click(await screen.findByRole("button", { name: "接受" }));
    await user.click(screen.getByRole("button", { name: /套用已接受/ }));

    await waitFor(() => expect(screen.queryByTestId("review-run")).not.toBeInTheDocument());
    expect(await mockKbApi.listContextCards("col-1")).toHaveLength(1);
  });

  it("dismisses a run so it leaves the queue without writing a card", async () => {
    await seedRun();
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole("button", { name: "略過" }));

    await waitFor(() => expect(screen.queryByTestId("review-run")).not.toBeInTheDocument());
    expect(await mockKbApi.listContextCards("col-1")).toHaveLength(0);
  });

  it("also lists the collection's open clarification questions (#377)", async () => {
    _seedDocQuestionMock({ id: "q1", term: "M4", question_text: "「M4」是什麼？" });
    renderTab();
    expect(await screen.findByText("「M4」是什麼？")).toBeInTheDocument();
    expect(screen.getByText("M4")).toBeInTheDocument();
  });

  it("answers a question and it leaves the inbox", async () => {
    _seedDocQuestionMock({ id: "q1", term: "M4", question_text: "「M4」是什麼？" });
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("「M4」是什麼？");

    await user.type(screen.getByRole("textbox"), "Metal 4");
    await user.click(screen.getByRole("button", { name: "送出" }));

    await waitFor(() => expect(screen.queryByText("「M4」是什麼？")).not.toBeInTheDocument());
  });
});
