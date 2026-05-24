// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { AskAgentLauncher } from "./AskAgentLauncher";

describe("AskAgentLauncher", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("opens the fast-chat drawer from the button", async () => {
    render(
      <MemoryRouter>
        <AskAgentLauncher client={mockKbApi} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /ask agent/i }));
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: /Ask the knowledge base/i })).toBeInTheDocument(),
    );
  });
});
