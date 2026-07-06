// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { useQuery } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetKbMock, _seedDocQuestionMock, mockKbApi } from "../../api/kbMock";
import { qk } from "../../api/queryKeys";
import { LocaleProvider } from "../../lib/i18n";
import { QueryWrap, makeTestQueryClient } from "../../test/queryWrapper";
import { CollectionReviewTab } from "./CollectionReviewTab";

const renderTab = () =>
  render(<CollectionReviewTab collectionId="col-1" client={mockKbApi} />, { wrapper: QueryWrap });

/** Seed a finalized run (the mock completes generation synchronously). The card's
 * title is the doc's stem, so a distinctive filename gives a findable title. */
async function seedRun() {
  await mockKbApi.uploadDocument("col-1", new File(["RZ3 is the third zone"], "reflow.md"));
  return mockKbApi.generateContextCards("col-1", ["col-1/me/reflow.md"]);
}

describe("<CollectionReviewTab /> (#415 → #481 table)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("shows an empty state when nothing is awaiting review", async () => {
    renderTab();
    expect(await screen.findByText("目前沒有待審核項目。")).toBeInTheDocument();
  });

  it("localizes the panel — English under an English locale, no hardcoded Chinese (#456)", async () => {
    localStorage.setItem("ws.locale", "en");
    render(
      <LocaleProvider>
        <CollectionReviewTab collectionId="col-1" client={mockKbApi} />
      </LocaleProvider>,
      { wrapper: QueryWrap },
    );
    expect(await screen.findByText("Nothing to review right now.")).toBeInTheDocument();
    expect(screen.queryByText("目前沒有待審核項目。")).not.toBeInTheDocument();
  });

  it("lists a finalized run's card proposal as a table row", async () => {
    await seedRun();
    renderTab();
    expect(await screen.findByText("reflow", { selector: ".rvw__title" })).toBeInTheDocument();
  });

  it("accepts a card, applies it — writes a card, drops from the queue, refreshes Cards", async () => {
    await seedRun();
    const client = makeTestQueryClient();
    function CardsProbe() {
      const { data = [] } = useQuery({
        queryKey: qk.kb.contextCards("col-1"),
        queryFn: () => mockKbApi.listContextCards("col-1"),
      });
      return <div data-testid="cards-count">{data.length}</div>;
    }
    const user = userEvent.setup();
    render(
      <QueryWrap client={client}>
        <CollectionReviewTab collectionId="col-1" client={mockKbApi} />
        <CardsProbe />
      </QueryWrap>,
    );
    await waitFor(() => expect(screen.getByTestId("cards-count")).toHaveTextContent("0"));

    await user.click(await screen.findByRole("button", { name: "接受" }));
    await user.click(await screen.findByLabelText("選取"));
    await user.click(screen.getByRole("button", { name: /套用選取/ }));

    await waitFor(() => expect(screen.queryAllByText("reflow")).toHaveLength(0));
    expect(await mockKbApi.listContextCards("col-1")).toHaveLength(1);
    await waitFor(() => expect(screen.getByTestId("cards-count")).toHaveTextContent("1"));
  });

  it("lists the collection's open clarification questions and answers one (#377)", async () => {
    _seedDocQuestionMock({ id: "q1", term: "M4", question_text: "「M4」是什麼？" });
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByText("「M4」是什麼？")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "回答" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(dialog.querySelector("textarea")!, "Metal 4");
    await user.click(dialog.querySelector("button.btn[data-variant='primary']")!);

    await waitFor(() => expect(screen.queryByText("「M4」是什麼？")).not.toBeInTheDocument());
  });
});
