// @vitest-environment happy-dom
/**
 * ModelEffortPicker — the composer's combined model + effort control
 * (design handoff 3.0): one chip in the input row; clicking it opens an
 * upward popover with a model list (name, blurb, default chip), a
 * reasoning-effort segmented control, and — on the KB surface — the
 * knowledge-search depth. Model selection semantics stay with the
 * caller (RCA persists to the investigation, KB is per-message), so
 * the component only reports the pick.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getReasoningEffort } from "../lib/reasoningEffort";
import { getStored } from "../lib/kbEnhancementMode";
import { renderWithQuery } from "../test/queryWrapper";
import { ModelEffortPicker } from "./ModelEffortPicker";

const MODELS = [
  {
    name: "qwen3-local",
    model: "ollama_chat/qwen3:14b",
    description: "Local model — private. Solid default.",
  },
  {
    name: "claude-opus",
    model: "claude-opus-4-7",
    description: "Deepest reasoning for tricky chains.",
  },
];

describe("ModelEffortPicker", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("chip shows the active model and opens the list with blurbs", async () => {
    const onSelect = vi.fn();
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={onSelect} />,
    );
    // selectedName=null → the first entry is the active default.
    const chip = screen.getByRole("button", { name: /model and effort/i });
    expect(chip).toHaveTextContent("qwen3-local");

    await userEvent.click(chip);
    expect(screen.getByText("claude-opus")).toBeInTheDocument();
    expect(screen.getByText(/deepest reasoning/i)).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument(); // first-entry chip

    await userEvent.click(screen.getByText("claude-opus"));
    expect(onSelect).toHaveBeenCalledWith("claude-opus");
  });

  it("effort segments persist the shared sticky value and show on the chip", async () => {
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName="claude-opus" onSelectModel={() => {}} />,
    );
    const chip = screen.getByRole("button", { name: /model and effort/i });
    expect(chip).toHaveTextContent("auto"); // no stored effort → model default

    await userEvent.click(chip);
    await userEvent.click(screen.getByRole("button", { name: /^high$/i }));

    expect(getReasoningEffort()).toBe("high");
    expect(chip).toHaveTextContent("high");
  });

  it("knowledge-search depth section only renders for the KB surface", async () => {
    const { unmount } = renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /model and effort/i }));
    expect(screen.queryByText(/search depth/i)).not.toBeInTheDocument();
    unmount();

    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} retrieval />,
    );
    await userEvent.click(screen.getByRole("button", { name: /model and effort/i }));
    expect(screen.getByText(/search depth/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /thorough/i }));
    expect(getStored().mode).toBe("thorough");
  });

  it("advanced sliders survive the redesign — tweaking one flips to custom", async () => {
    // The old depth picker's Advanced disclosure (expand / hyde /
    // rerank) must not be lost in the new popover: power users tune
    // exact values, and the mode auto-flips to custom.
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} retrieval />,
    );
    await userEvent.click(screen.getByRole("button", { name: /model and effort/i }));
    await userEvent.click(screen.getByRole("button", { name: /advanced/i }));

    const expand = screen.getByRole("slider", { name: /expand/i });
    expect(expand).toBeInTheDocument();
    fireEvent.change(expand, { target: { value: "3" } });

    const stored = getStored();
    expect(stored.mode).toBe("custom");
    expect(stored.custom?.expand).toBe(3);
    // rerank toggle is there too.
    expect(screen.getByRole("checkbox", { name: /rerank/i })).toBeInTheDocument();
  });
});
