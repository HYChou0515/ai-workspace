/**
 * The card's login page (docs/plan-env-request-card.md N6) holds a password
 * while it is typed: a deliberate exit asks once (#779), an untouched one
 * closes at once.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
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

function open() {
  const onClose = vi.fn();
  renderWithQuery(
    <EnvLoginModal
      slug="rca"
      itemId="i1"
      provider={erp}
      onClose={onClose}
      client={{ resolveEnvProvider: vi.fn() }}
      privateClient={{ get: vi.fn(), put: vi.fn() }}
    />,
  );
  return onClose;
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
});
