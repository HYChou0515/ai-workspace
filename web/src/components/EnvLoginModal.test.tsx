/**
 * The card's login page (docs/plan-env-request-card.md N6) holds a password
 * while it is typed: a deliberate exit asks once (#779), an untouched one
 * closes at once.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EnvProvider } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { EnvLoginModal } from "./EnvLoginModal";

const erp: EnvProvider = {
  id: "erp",
  label: "ERP",
  produces: ["ERP_TOKEN"],
  inputs: [{ name: "password", label: "密碼", secret: true }],
};

function open({
  got = { ERP_TOKEN: "tok" } as Record<string, string>,
  stored = {} as Record<string, string>,
} = {}) {
  const onClose = vi.fn();
  const put = vi.fn(async () => {});
  renderWithQuery(
    <EnvLoginModal
      slug="rca"
      itemId="i1"
      provider={erp}
      onClose={onClose}
      client={{ resolveEnvProvider: vi.fn(async () => got) }}
      privateClient={{ get: vi.fn(async () => ({ values: stored, auto: {} })), put }}
    />,
  );
  return Object.assign(onClose, { put });
}

async function signIn() {
  fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByTestId("env-cred-submit"));
  await screen.findByTestId("env-login-save");
}

afterEach(cleanup);

describe("EnvLoginModal", () => {
  it("asks before dropping a typed password, and keeps it when told to", async () => {
    const onClose = open();
    fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "pw" } });

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByTestId("env-cred-password")).toHaveValue("pw");
  });

  it("closes on Escape without asking when nothing was typed", () => {
    const onClose = open();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
    expect(screen.queryByTestId("dialog-action-keep")).toBeNull();
  });

  it("asks before dropping a token it signed in for but has not saved", async () => {
    const onClose = open();
    await signIn();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
    expect(await screen.findByTestId("dialog-action-keep")).toBeInTheDocument();
  });

  it("closes from the form's own Cancel — a login page is not left half-empty", async () => {
    const onClose = open();

    fireEvent.click(screen.getByTestId("env-cred-cancel"));

    expect(onClose).toHaveBeenCalled();
  });

  it("saves onto what is stored now, and leaves out a blank the login returned (N5)", async () => {
    // The store holds a value the card's cache never saw: the save must read
    // it fresh, or the PUT — which replaces the layer — would drop it.
    const onClose = open({ got: { ERP_TOKEN: "tok", ERP_SITE: "" }, stored: { NEW: "n" } });
    await signIn();

    fireEvent.click(screen.getByTestId("env-login-save"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onClose.put).toHaveBeenCalledWith("rca", "i1", { NEW: "n", ERP_TOKEN: "tok" });
  });

  it("is the login form alone — no one-choice provider button above it", () => {
    open();

    expect(screen.queryByTestId("env-provider-erp")).toBeNull();
    expect(screen.getByTestId("env-cred-password")).toBeInTheDocument();
  });

  it("keeps its top edge put when the form gives way to the result", () => {
    // Centred, the shorter result view moved the whole dialog down under the
    // pointer that had just pressed submit.
    open();

    const backdrop = screen.getByTestId("env-login-modal").parentElement;
    expect(backdrop).toHaveStyle({ alignItems: "flex-start" });
  });

  it("names where Save puts it the way the panel's tab does", async () => {
    open();
    await signIn();

    expect(screen.getByText(/Private/)).toBeInTheDocument();
  });
});
