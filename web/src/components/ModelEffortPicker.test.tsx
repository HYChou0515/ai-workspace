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
import { getKbSearchMax } from "../lib/kbSearchMax";
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
    const chip = screen.getByRole("button", { name: /模型與思考深度/ });
    expect(chip).toHaveTextContent("qwen3-local");

    await userEvent.click(chip);
    expect(screen.getByText("claude-opus")).toBeInTheDocument();
    expect(screen.getByText(/deepest reasoning/i)).toBeInTheDocument();
    expect(screen.getByText("預設")).toBeInTheDocument(); // first-entry chip

    await userEvent.click(screen.getByText("claude-opus"));
    expect(onSelect).toHaveBeenCalledWith("claude-opus");
  });

  it("#160: never exposes the raw model id", async () => {
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));
    expect(screen.queryByText("ollama_chat/qwen3:14b")).not.toBeInTheDocument();
    expect(screen.queryByText("claude-opus-4-7")).not.toBeInTheDocument();
  });

  it("#160: effort is three segments defaulting to the lightest (Auto removed)", async () => {
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName="claude-opus" onSelectModel={() => {}} />,
    );
    const chip = screen.getByRole("button", { name: /模型與思考深度/ });
    expect(chip).toHaveTextContent("快速"); // default low, no "auto"
    expect(chip).not.toHaveTextContent(/auto/i);

    await userEvent.click(chip);
    expect(screen.queryByRole("button", { name: /^auto$/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "深入" }));

    expect(getReasoningEffort()).toBe("high");
    expect(chip).toHaveTextContent("深入");
  });

  it("knowledge-search scope section only renders for the KB surface", async () => {
    const { unmount } = renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));
    expect(screen.queryByText("搜尋範圍")).not.toBeInTheDocument();
    unmount();

    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} retrieval />,
    );
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));
    expect(screen.getByText("搜尋範圍")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "徹底" }));
    expect(getStored().mode).toBe("thorough");
  });

  it("#246: model name uses a theme-aware text color (legible in dark mode)", async () => {
    // --ink is always near-black in both themes, so on a dark popover/chip it
    // vanished. The model name must use a token that flips with the theme.
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} />,
    );
    // Trigger chip (closed): the active model name.
    expect(screen.getByText("qwen3-local").style.color).toBe("var(--text-paper)");

    // Popover list item names must adapt too.
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));
    expect(screen.getByText("claude-opus").style.color).toBe("var(--text-paper)");
  });

  it("#256: trigger chip uses min-height so a larger system font can't clip it", () => {
    // Font scale (#226) grows rem text but not fixed px boxes — a fixed `height`
    // clipped the chip at large scale. min-height lets it grow with the text.
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} />,
    );
    const chip = screen.getByRole("button", { name: /模型與思考深度/ });
    expect(chip.style.minHeight).toBe("28px");
    expect(chip.style.height).toBe("");
  });

  it("advanced sliders survive the redesign with plain-language labels", async () => {
    // The old expand / hyde / rerank knobs must not be lost — power users
    // still tune exact values (mode auto-flips to custom) — but the labels
    // are now de-jargoned (#160).
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} retrieval />,
    );
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));
    await userEvent.click(screen.getByRole("button", { name: "進階" }));

    // No raw hyperparameter nouns leak into the UI.
    expect(screen.queryByText(/\bhyde\b/i)).not.toBeInTheDocument();

    const expand = screen.getByRole("slider", { name: /換句話多問幾種/ });
    expect(expand).toBeInTheDocument();
    fireEvent.change(expand, { target: { value: "3" } });

    const stored = getStored();
    expect(stored.mode).toBe("custom");
    expect(stored.custom?.expand).toBe(3);
    expect(screen.getByRole("checkbox", { name: /重新排序/ })).toBeInTheDocument();
  });

  it("#334: max-searches stepper only on the KB surface", async () => {
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));
    expect(screen.queryByLabelText("最多搜尋次數")).not.toBeInTheDocument();
  });

  it("#334: stepper adjusts the sticky max-searches and clamps at 0", async () => {
    renderWithQuery(
      <ModelEffortPicker models={MODELS} selectedName={null} onSelectModel={() => {}} retrieval />,
    );
    await userEvent.click(screen.getByRole("button", { name: /模型與思考深度/ }));

    const value = screen.getByLabelText("最多搜尋次數");
    expect(value).toHaveTextContent("3"); // default

    await userEvent.click(screen.getByRole("button", { name: "增加搜尋次數" }));
    expect(value).toHaveTextContent("4");
    expect(getKbSearchMax()).toBe(4);

    // Down to the floor: 4→3→2→1→0, then the − button is disabled (0 = no search).
    for (let i = 0; i < 5; i++) {
      await userEvent.click(screen.getByRole("button", { name: "減少搜尋次數" }));
    }
    expect(value).toHaveTextContent("0");
    expect(getKbSearchMax()).toBe(0);
    expect(screen.getByRole("button", { name: "減少搜尋次數" })).toBeDisabled();
  });
});
