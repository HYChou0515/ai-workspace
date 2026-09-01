// @vitest-environment happy-dom
/**
 * ContextBar (#739 P2) — how full this chat's context window is.
 *
 * The number it shows is anchored on what the provider itself reported, so it
 * counts the system prompt and tool schemas the estimator never sees. The bar
 * exists so a long conversation stops ending in a surprise: the user can watch
 * it fill and compact before the thread is cut.
 */

import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { renderWithQuery } from "../test/queryWrapper";
import { ContextBar } from "./ContextBar";

afterEach(cleanup);

describe("ContextBar", () => {
  it("shows how much of the window is in use against its ceiling", async () => {
    renderWithQuery(
      <ContextBar
        slug="rca"
        itemId="i1"
        chatId="c1"
        load={async () => ({ used: 20480, limit: 40960, measured: true })}
      />,
    );
    const el = await screen.findByTestId("chat-context");
    expect(el.textContent).toContain("20.5k");
    expect(el.textContent).toContain("41k");
  });

  it("shows no denominator when no ceiling is known", async () => {
    // `limit: null` means nothing credible declared a window. Drawing a bar
    // against an invented ceiling is the #624 disease — a number nobody
    // measured that everybody believes — so the usage stands alone.
    renderWithQuery(
      <ContextBar
        slug="rca"
        itemId="i1"
        chatId="c1"
        load={async () => ({ used: 9000, limit: null, measured: true })}
      />,
    );
    const el = await screen.findByTestId("chat-context");
    expect(el.textContent).toContain("9k");
    expect(el.textContent).not.toContain("/");
    expect(el.querySelector("[data-testid='chat-context-fill']")).toBeNull();
  });
});

describe("ContextBar honesty (#739 review)", () => {
  it("marks a figure nobody measured as an estimate", async () => {
    // `measured` was fetched, typed and documented — "so the UI never presents
    // a guess as a fact" — then read by nothing: an estimate and a
    // provider-measured count rendered identically. Removing the invented
    // denominator while leaving an unlabelled invented numerator is half the
    // #624 lesson.
    renderWithQuery(
      <ContextBar
        slug="rca"
        itemId="i1"
        chatId="c1"
        load={async () => ({ used: 9000, limit: 40960, measured: false })}
      />,
    );
    const el = await screen.findByTestId("chat-context");
    expect(el.textContent).toContain("~9k");
  });

  it("does not hedge a figure the provider itself reported", async () => {
    renderWithQuery(
      <ContextBar
        slug="rca"
        itemId="i1"
        chatId="c1"
        load={async () => ({ used: 9000, limit: 40960, measured: true })}
      />,
    );
    const el = await screen.findByTestId("chat-context");
    expect(el.textContent).not.toContain("~");
  });
});
