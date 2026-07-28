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

const enter = (el: HTMLElement) => fireEvent.keyDown(el, { key: "Enter" });

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

  it("sends on the click — every question is single-choice, so the pick IS the answer", () => {
    // There is no multi-select anywhere (no such field in the tool schema or
    // here), so one click fully answers one question. Making the user then press
    // 送出 was a second click that decided nothing.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /SQLite/ }));

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content, answers }] = onAnswer.mock.calls[0];
    expect(content).toContain("SQLite");
    expect(answers).toBe("call_1");
  });

  it("offers no 送出 button to press", () => {
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /送出/ })).toBeNull();
  });

  it("carries the picked option's OWN note into the answer", () => {
    // The note is typed BEFORE the pick commits: reaching for the box selects
    // that option without sending, so there is room to finish the sentence.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.change(screen.getByLabelText("補充:Postgres"), {
      target: { value: "we already run one" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Postgres/ }));

    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("Postgres");
    expect(content).toContain("we already run one");
  });

  it("does not send just because a note field took focus", () => {
    // Focus is how the note box marks which option it belongs to. If that
    // counted as a decision, clicking into the box would send the empty note
    // it was opened to write — the exact hazard the old 送出 button guarded.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.focus(screen.getByLabelText("補充:Postgres"));
    fireEvent.change(screen.getByLabelText("補充:Postgres"), { target: { value: "half a th" } });

    expect(onAnswer).not.toHaveBeenCalled();
  });

  it("sends the note on Enter, so typing never needs a button", () => {
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    const note = screen.getByLabelText("補充:SQLite");
    fireEvent.focus(note);
    fireEvent.change(note, { target: { value: "single node is enough" } });
    enter(note);

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("SQLite");
    expect(content).toContain("single node is enough");
  });

  it("waits for the LAST question of a multi-question card, then sends once", () => {
    // A card may carry up to five questions (_MAX_QUESTIONS). Answering one of
    // them is not an answer to the card, so the click that completes the set is
    // the one that sends — and it sends all of them together, once.
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
    expect(onAnswer).not.toHaveBeenCalled(); // one of two — the card is not answered yet

    fireEvent.click(screen.getByRole("button", { name: /2.*No/ }));

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("PDF");
    expect(content).toContain("No");
  });

  it("lets an unanswered question be changed before the last one commits", () => {
    // Until the set is complete nothing has been sent, so a pick is still a
    // draft — the second thought about question 1 is the one that goes.
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
    fireEvent.click(screen.getByRole("button", { name: /HTML/ }));
    fireEvent.click(screen.getByRole("button", { name: /1.*Yes/ }));

    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("HTML");
    expect(content).not.toContain("PDF");
  });

  it("latches on its own send, without waiting for the answer to come back", () => {
    // `answered` comes from the persisted transcript, a round trip away. While
    // it is in flight the options would still be clickable — and now that a
    // click sends, a second one is a second turn on a question already answered.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /Postgres/ }));

    expect(screen.queryByRole("button", { name: /SQLite/ })).toBeNull();
    expect(screen.getByTestId("ask-user-answered")).toBeTruthy();
    expect(onAnswer).toHaveBeenCalledTimes(1);
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

    expect(onAnswer).toHaveBeenCalledTimes(1);
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

    const own = screen.getByLabelText("自己回答");
    fireEvent.change(own, { target: { value: "DuckDB, we already ship it" } });
    expect(onAnswer).not.toHaveBeenCalled(); // still mid-sentence
    enter(own);

    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("DuckDB");
    // A free answer is not one of the options, so it must not read as a pick.
    expect(content).not.toMatch(/→ Postgres|→ SQLite/);
  });

  it("does not let a stray Enter in the empty box erase a pick", () => {
    // Typing a free answer replaces the pick — that is the point of it. Pressing
    // Enter on an EMPTY box has typed no answer to replace it with, so taking
    // the pick away would leave the user with neither.
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
    enter(screen.getAllByLabelText("自己回答")[0]);
    expect(onAnswer).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /2.*No/ }));

    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("PDF"); // still there
  });

  it("does not send an empty answer of the user's own", () => {
    // Enter in an empty box is a stray keystroke, not an answer — sending
    // "(未選擇)" would spend the turn on nothing.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    enter(screen.getByLabelText("自己回答"));

    expect(onAnswer).not.toHaveBeenCalled();
  });
});
