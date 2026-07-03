// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { qk } from "../../api/queryKeys";
import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import {
  makeTestQueryClient,
  QueryWrap,
  renderWithQuery,
} from "../../test/queryWrapper";
import { AutoGenerateCards } from "./AutoGenerateCards";
import { fetchAllDocs } from "./useCollectionDocs";

const renderModal = (onClose: () => void = () => {}) =>
  render(
    <AutoGenerateCards collectionId="col-1" client={mockKbApi} onClose={onClose} />,
    { wrapper: QueryWrap },
  );

describe("AutoGenerateCards (#175)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("picks a document, generates, accepts a proposal, and commits a card", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["RZ3 is the third zone"], "reflow.md"));
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /自動生成/ }));

    const proposal = await screen.findByTestId("cardgen-proposal");
    expect(proposal).toHaveTextContent("reflow.md"); // provenance "依據" is shown
    expect(proposal).toHaveTextContent("new"); // new-vs-update badge

    await user.click(screen.getByRole("button", { name: "接受" }));
    await user.click(screen.getByRole("button", { name: /套用已接受/ }));

    expect(await screen.findByTestId("cardgen-committed")).toBeInTheDocument();
    expect(await mockKbApi.listContextCards("col-1")).toHaveLength(1);
  });

  it("keeps the picker populated when the shared documents cache already holds a bare array (#394)", async () => {
    // The collection page's index-status strip is always live and writes the
    // shared qk.kb.documents(cid) key as a bare KbDocument[] (via fetchAllDocs).
    // The picker must read through that same shape — not a {items} page — or it
    // shows "No documents." even though the collection is full. Priming the key
    // exactly as the strip would reproduces #394.
    await mockKbApi.uploadDocument("col-1", new File(["x"], "shared.md"));
    const client = makeTestQueryClient();
    client.setQueryData(
      qk.kb.documents("col-1"),
      await fetchAllDocs(mockKbApi, "col-1"),
    );

    renderWithQuery(
      <AutoGenerateCards collectionId="col-1" client={mockKbApi} onClose={() => {}} />,
      client,
    );

    expect(await screen.findByText("shared.md")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeInTheDocument();
  });

  it("offers a todo.md bulk view of the proposals", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "a.md"));
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /自動生成/ }));
    await screen.findByTestId("cardgen-proposal");

    await user.click(screen.getByRole("button", { name: "todo.md" }));
    expect(screen.getByLabelText("todo.md")).toBeInTheDocument();
  });

  it("does not commit when nothing is accepted", async () => {
    await mockKbApi.uploadDocument("col-1", new File(["x"], "a.md"));
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /自動生成/ }));
    await screen.findByTestId("cardgen-proposal");

    expect(screen.getByRole("button", { name: /套用已接受/ })).toBeDisabled();
  });
});
