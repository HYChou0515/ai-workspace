/**
 * "My environment variables" (`docs/plan-personal-env.md`) — a person's values
 * for every item. Signing in here (or in any item) is where a token goes, so a
 * token that expired is renewed in one place (D3, D6). Values can also be typed
 * (D8). Each value says when it was last set; the page never shows a credential
 * typed into a sign-in.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../test/queryWrapper";
import { MyEnvPage } from "./MyEnvPage";

afterEach(cleanup);

const DAY = 86_400_000;

function open({
  values = {} as Record<string, string>,
  updated = {} as Record<string, number>,
  providers = [
    {
      id: "erp",
      label: "ERP login",
      produces: ["ERP_TOKEN"],
      inputs: [{ name: "password", label: "Password", secret: true }],
    },
  ],
} = {}) {
  const client = {
    get: vi.fn(async () => ({ values, updated })),
    put: vi.fn(async (next: Record<string, string>) => ({ values: next, updated: {} })),
    providers: vi.fn(async () => providers),
    resolve: vi.fn(async () => ({ ERP_TOKEN: "fresh" })),
  };
  renderWithQuery(<MyEnvPage client={client} />);
  return client;
}

describe("MyEnvPage", () => {
  it("lists my values by name, masked, with when each was set", async () => {
    open({
      values: { ERP_TOKEN: "secret-value", API_KEY: "k" },
      updated: { ERP_TOKEN: Date.now() - 3 * DAY, API_KEY: Date.now() },
    });

    const row = await screen.findByTestId("my-env-row-ERP_TOKEN");
    expect(row).toHaveTextContent("ERP_TOKEN");
    expect(row).toHaveTextContent("3");
    expect(row).not.toHaveTextContent("secret-value");
    expect(screen.getByTestId("my-env-row-API_KEY")).toBeInTheDocument();
  });

  it("says what to do when there is nothing yet", async () => {
    open();
    expect(await screen.findByTestId("my-env-empty")).toBeInTheDocument();
  });

  it("adds a value I type, keeping the others", async () => {
    const client = open({ values: { OLD: "o" } });
    await screen.findByTestId("my-env-row-OLD");

    fireEvent.change(screen.getByTestId("my-env-new-name"), { target: { value: "API_KEY" } });
    fireEvent.change(screen.getByTestId("my-env-new-value"), { target: { value: "k-1" } });
    fireEvent.click(screen.getByTestId("my-env-add"));

    await waitFor(() => expect(client.put).toHaveBeenCalledWith({ OLD: "o", API_KEY: "k-1" }));
  });

  it("refuses a name a tool could not be given", async () => {
    const client = open();
    await screen.findByTestId("my-env-empty");

    fireEvent.change(screen.getByTestId("my-env-new-name"), { target: { value: "not a name" } });
    fireEvent.change(screen.getByTestId("my-env-new-value"), { target: { value: "v" } });

    expect(screen.getByTestId("my-env-add")).toBeDisabled();
    expect(client.put).not.toHaveBeenCalled();
  });

  it("removes one value and keeps the rest", async () => {
    const client = open({ values: { A: "1", B: "2" } });

    fireEvent.click(within(await screen.findByTestId("my-env-row-A")).getByTestId("my-env-remove"));

    await waitFor(() => expect(client.put).toHaveBeenCalledWith({ B: "2" }));
  });

  it("saves what a sign-in returns at once, and never the credential", async () => {
    const client = open({ values: { OTHER: "kept" } });

    fireEvent.click(await screen.findByTestId("env-provider-erp"));
    fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "hunter2" } });
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    await waitFor(() =>
      expect(client.put).toHaveBeenCalledWith({ OTHER: "kept", ERP_TOKEN: "fresh" }),
    );
    expect(client.resolve).toHaveBeenCalledWith("erp", { password: "hunter2" });
    expect(JSON.stringify(client.put.mock.calls)).not.toContain("hunter2");
  });
});
