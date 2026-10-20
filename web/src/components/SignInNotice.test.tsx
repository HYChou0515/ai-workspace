/**
 * `docs/plan-env-token-refresh.md` D6: a sign-in whose refresh token is dead
 * is told in the thread with that sign-in's own button — the remedy, picked
 * for the person (NN/g, Error-Message Guidelines). Signing in again writes back
 * to the layer the dead token lived in (D3): my environment variables, or this
 * item's Private; a shared token is the item's, so the button opens the panel.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EnvProvider } from "../api/types";
import { type ChatEnv, ChatItemProvider } from "../hooks/chatItem";
import { renderWithQuery } from "../test/queryWrapper";

import { SignInNotice } from "./SignInNotice";

const erp: EnvProvider = {
  id: "erp",
  label: "ERP",
  produces: ["ERP_TOKEN", "ERP_REFRESH"],
  inputs: [
    { name: "user", label: "帳號", secret: false },
    { name: "password", label: "密碼", secret: true },
  ],
};

function draw(layer: "personal" | "private" | "shared", { chat = true } = {}) {
  const host: ChatEnv = {
    shared: {},
    policy: {},
    open: vi.fn(),
    retry: vi.fn(() => true),
    answered: () => false,
  };
  const privatePut = vi.fn(async () => {});
  const personalPut = vi.fn(async (values: Record<string, string>) => ({ values, updated: {} }));
  const notice = (
    <SignInNotice
      provider="erp"
      layer={layer}
      text="Your sign-in to ERP has expired. Sign in again."
      client={{
        getEnvProviders: async () => [erp],
        resolveEnvProvider: vi.fn(async () => ({ ERP_TOKEN: "t2", ERP_REFRESH: "r2" })),
      }}
      privateClient={{ get: async () => ({ values: { OTHER: "o" }, auto: {} }), put: privatePut }}
      personalClient={{ get: async () => ({ values: { MINE: "m" }, updated: {} }), put: personalPut }}
    />
  );
  renderWithQuery(
    chat ? <ChatItemProvider value={{ slug: "rca", itemId: "i1", env: host }}>{notice}</ChatItemProvider> : notice,
  );
  return { host, privatePut, personalPut };
}

async function signIn() {
  fireEvent.click(await screen.findByRole("button", { name: /登入 ERP|Sign in to ERP/ }));
  fireEvent.change(await screen.findByTestId("env-cred-user"), { target: { value: "amy" } });
  fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByTestId("env-cred-submit"));
  fireEvent.click(await screen.findByTestId("env-login-save"));
}

afterEach(cleanup);

describe("a sign_in entry in the thread", () => {
  it("is drawn as the sign-in notice, not as a chat message", async () => {
    const { EntryView } = await import("./AgentEntryView");
    renderWithQuery(
      <EntryView
        entry={{ kind: "sign_in", text: "Your sign-in to ERP has expired.", provider: "erp", layer: "personal" }}
      />,
    );
    expect(await screen.findByTestId("sign-in-notice")).toHaveTextContent("Your sign-in to ERP has expired.");
  });
});

describe("SignInNotice", () => {
  it("says which sign-in expired, by its label", async () => {
    draw("personal");
    expect(await screen.findByTestId("sign-in-notice")).toHaveTextContent("ERP");
  });

  it("signs in back into my environment variables when the dead token lived there", async () => {
    const { personalPut, privatePut } = draw("personal");

    await signIn();

    await waitFor(() =>
      expect(personalPut).toHaveBeenCalledWith({ MINE: "m", ERP_TOKEN: "t2", ERP_REFRESH: "r2" }),
    );
    expect(privatePut).not.toHaveBeenCalled();
  });

  it("signs in back into this item's Private when the dead token lived there", async () => {
    const { personalPut, privatePut } = draw("private");

    await signIn();

    await waitFor(() =>
      expect(privatePut).toHaveBeenCalledWith("rca", "i1", { OTHER: "o", ERP_TOKEN: "t2", ERP_REFRESH: "r2" }),
    );
    expect(personalPut).not.toHaveBeenCalled();
  });

  it("opens the panel for a shared token: it is the item's, not one person's", async () => {
    const { host } = draw("shared");

    fireEvent.click(await screen.findByRole("button", { name: /登入 ERP|Sign in to ERP/ }));

    expect(host.open).toHaveBeenCalledWith({ name: "ERP_TOKEN" });
    expect(screen.queryByTestId("env-login-modal")).not.toBeInTheDocument();
  });

  it("outside a chat that can open the panel, says it without a button", async () => {
    draw("personal", { chat: false });
    expect(await screen.findByTestId("sign-in-notice")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
