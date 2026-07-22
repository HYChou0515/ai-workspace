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

const send = () => fireEvent.click(screen.getByRole("button", { name: /送出|Send/i }));

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

  it("numbers the options so they can be referred to as 1, 2, 3", () => {
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);
    // The badge sits in the option button, so the button's accessible name
    // includes its number.
    expect(screen.getByRole("button", { name: /1.*Postgres/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /2.*SQLite/ })).toBeTruthy();
  });

  it("gives every option its OWN supplement field", () => {
    // One note input per option, not one shared box — a note about Postgres is
    // about Postgres. Keyed by the option's label so they stay independent.
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);
    expect(screen.getByLabelText("補充:Postgres")).toBeTruthy();
    expect(screen.getByLabelText("補充:SQLite")).toBeTruthy();
  });

  it("selects on click and commits on 送出 — not on the first click", () => {
    // With a per-option note to type, sending on the click would fire before the
    // person finished. So a click highlights; 送出 sends.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /SQLite/ }));
    expect(onAnswer).not.toHaveBeenCalled(); // the click did not send

    send();
    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content, answers }] = onAnswer.mock.calls[0];
    expect(content).toContain("SQLite");
    expect(answers).toBe("call_1");
  });

  it("carries the picked option's OWN note into the answer", () => {
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /Postgres/ }));
    fireEvent.change(screen.getByLabelText("補充:Postgres"), {
      target: { value: "we already run one" },
    });
    send();

    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("Postgres");
    expect(content).toContain("we already run one");
  });

  it("carries every answer of a multi-question card in one send", () => {
    const onAnswer = vi.fn();
    render(
      <AskUserCard
        call={{
          ...oneQuestion,
          args: {
            questions: [
              { question: "Format?", options: [{ label: "PDF" }, { label: "HTML" }] },
              { question: "Include charts?", options: [{ label: "Yes" }, { label: "No" }] },
            ],
          },
        }}
        onAnswer={onAnswer}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));
    fireEvent.click(screen.getByRole("button", { name: /2.*No/ }));
    send();

    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("PDF");
    expect(content).toContain("No");
  });

  it("stops offering the buttons once the question is answered", () => {
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} answered="SQLite" />);
    expect(screen.queryByRole("button", { name: /Postgres/ })).toBeNull();
    expect(screen.getByText(/SQLite/)).toBeTruthy();
  });
});

describe("AskUserCard malformed args", () => {
  /* The shape is the backend's contract (a strict tool schema names the fields),
   * so the card EXPECTS `{question, options:[{label, description}]}` rather than
   * guessing around a loose one — papering over bad args here would just move the
   * contract into the FE, where it drifts. The one concession is not throwing:
   * genuinely broken args render nothing, because a card that throws would take
   * the whole transcript down. */

  it("renders nothing rather than throwing on broken args", () => {
    const { container } = render(
      <AskUserCard call={{ ...oneQuestion, args: {} }} onAnswer={vi.fn()} />,
    );
    expect(container.textContent).toBe("");
  });
});

describe("AskUserCard escape hatches", () => {
  /* The options are the agent's guess. The card always offers a way to say
   * something else and a way to reject the question itself — added by the card,
   * not the agent, so they are there even when a small model forgets them. */

  it("always offers a way to say the question makes no sense", () => {
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /看不懂/ }));
    send();

    const [{ content }] = onAnswer.mock.calls[0];
    // Not an answer — a rejection of the question, so the agent re-asks
    // instead of proceeding on a choice the user never made.
    expect(content).toMatch(/看不懂/);
    expect(content).not.toMatch(/Postgres|SQLite/);
    for (const cause of [/繞/, /術語/, /沒重點/, /沒看過的詞/]) {
      expect(content).toMatch(cause);
    }
    expect(content).toMatch(/重問同一題/);
  });

  it("always offers an answer of the user's own", () => {
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.change(screen.getByLabelText("自己回答"), {
      target: { value: "DuckDB, we already ship it" },
    });
    send();

    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("DuckDB");
    // A free answer is not one of the options, so it must not read as a pick.
    expect(content).not.toMatch(/→ Postgres|→ SQLite/);
  });
});
