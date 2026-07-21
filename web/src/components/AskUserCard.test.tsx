/**
 * The `ask_user` question card (grill-me).
 *
 * The agent's question arrives as an ordinary tool call whose `args` carry the
 * questions and their options. This card turns that into something the user
 * can click, instead of a paragraph they have to answer by typing.
 *
 * Answering sends an ordinary message that records which question it answers
 * (`answers` = the tool call id). Nothing waits for it — the next turn picks
 * it up from the transcript.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AskUserCard } from "./AskUserCard";

const oneQuestion = {
  call_id: "call_1",
  name: "ask_user",
  status: "done" as const,
  args: {
    questions: [
      {
        question: "Which storage backend?",
        options: [
          { label: "Postgres", description: "Durable, needs a server" },
          { label: "SQLite", description: "Zero setup, single node" },
        ],
      },
    ],
  },
};

afterEach(cleanup);

describe("AskUserCard", () => {
  it("renders the question and each option's meaning", () => {
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);

    expect(screen.getByText("Which storage backend?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Postgres/ })).toBeTruthy();
    // The description is what lets the user decide without asking what the
    // options mean — dropping it would leave two bare labels.
    expect(screen.getByText(/Durable, needs a server/)).toBeTruthy();
  });

  it("answers with the option's text and the question it answers", () => {
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /SQLite/ }));

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content, answers }] = onAnswer.mock.calls[0];
    // Readable in the transcript, and unambiguous about which question it is.
    expect(content).toContain("SQLite");
    expect(answers).toBe("call_1");
  });

  it("carries every answer of a multi-question card in one send", () => {
    const onAnswer = vi.fn();
    render(
      <AskUserCard
        call={{
          ...oneQuestion,
          args: {
            questions: [
              {
                question: "Format?",
                options: [
                  { label: "PDF", description: "" },
                  { label: "HTML", description: "" },
                ],
              },
              {
                question: "Include charts?",
                options: [
                  { label: "Yes", description: "" },
                  { label: "No", description: "" },
                ],
              },
            ],
          },
        }}
        onAnswer={onAnswer}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));
    fireEvent.click(screen.getByRole("button", { name: /^No$/ }));
    fireEvent.click(screen.getByRole("button", { name: /送出|Send/i }));

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("PDF");
    expect(content).toContain("No");
  });

  it("lets the user send without answering every question", () => {
    /* Forcing an answer to all of them makes people pick something to escape,
     * and a made-up preference is worse than a missing one. */
    const onAnswer = vi.fn();
    render(
      <AskUserCard
        call={{
          ...oneQuestion,
          args: {
            questions: [
              {
                question: "Format?",
                options: [
                  { label: "PDF", description: "" },
                  { label: "HTML", description: "" },
                ],
              },
              {
                question: "Include charts?",
                options: [
                  { label: "Yes", description: "" },
                  { label: "No", description: "" },
                ],
              },
            ],
          },
        }}
        onAnswer={onAnswer}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));
    fireEvent.click(screen.getByRole("button", { name: /送出|Send/i }));

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("PDF");
    // The unanswered one is reported as unanswered rather than silently dropped,
    // so the agent knows it still doesn't have that decision.
    expect(content).toMatch(/Include charts\?/);
  });

  it("stops offering the buttons once the question is answered", () => {
    /* An answered question must not invite a second answer — two tabs, or a
     * scroll back, would otherwise produce two contradictory answers. */
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} answered="SQLite" />);

    expect(screen.queryByRole("button", { name: /Postgres/ })).toBeNull();
    expect(screen.getByText(/SQLite/)).toBeTruthy();
  });

  it("renders nothing for a malformed call rather than throwing", () => {
    /* The args come from a model. A card that throws takes the whole transcript
     * down with it. */
    const { container } = render(
      <AskUserCard call={{ ...oneQuestion, args: {} }} onAnswer={vi.fn()} />,
    );

    expect(container.textContent).toBe("");
  });
});
