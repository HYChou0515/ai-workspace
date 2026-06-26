// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { AutoGenerateCards } from "./AutoGenerateCards";

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
