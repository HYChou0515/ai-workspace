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

const twoQuestions = {
  ...oneQuestion,
  args: {
    questions: [
      {
        header: "Format",
        question: "Format?",
        options: [{ label: "PDF" }, { label: "HTML" }],
      },
      {
        header: "Charts",
        question: "Include charts?",
        options: [{ label: "Yes" }, { label: "No" }],
      },
    ],
  },
};

const send = () => fireEvent.click(screen.getByRole("button", { name: /送出|Send/i }));
const openTab = (name: RegExp) => fireEvent.click(screen.getByRole("tab", { name }));

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

    // These questions predate the `header` field, so their tabs are numbered —
    // which is also how every question already sitting in a transcript renders.
    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));
    openTab(/^2/);
    fireEvent.click(screen.getByRole("button", { name: /2.*No/ }));
    send();

    // One send carries both, so the answer to a tab the user left behind is
    // still in it.
    const [{ content }] = onAnswer.mock.calls[0];
    expect(content).toContain("PDF");
    expect(content).toContain("No");
  });

  it("takes ONLY the 送出 button away once it has been pressed", () => {
    // The button's whole job is ahead of the send. Left standing afterwards it
    // says nothing happened, and the only thing it can still do is send a
    // second answer. Everything else stays exactly where it was — the question
    // and the chosen option are the record of what was just sent.
    const onAnswer = vi.fn();
    render(<AskUserCard call={oneQuestion} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByRole("button", { name: /SQLite/ }));
    send();

    expect(screen.queryByRole("button", { name: /送出/ })).toBeNull();
    expect(screen.getByText("Which storage backend?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /SQLite/ })).toHaveAttribute("aria-pressed", "true");
    expect(onAnswer).toHaveBeenCalledTimes(1);
  });

  it("does not wait for the answer to come back before hiding 送出", () => {
    // `answered` arrives from the persisted transcript a round trip later. If
    // the button waited for it, the whole gap between press and echo is time
    // the card spends inviting a second press.
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /SQLite/ }));
    send();

    // No `answered` prop was ever passed — the card knows on its own.
    expect(screen.queryByRole("button", { name: /送出/ })).toBeNull();
  });

  it("stops taking a different answer once it has been sent", () => {
    // With no 送出 left, a click that still moved the highlight would leave the
    // card showing a choice that was never sent.
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /SQLite/ }));
    send();
    fireEvent.click(screen.getByRole("button", { name: /Postgres/ }));

    expect(screen.getByRole("button", { name: /Postgres/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /SQLite/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("補充:SQLite")).toHaveAttribute("readonly");
  });

  it("stops offering the buttons once the question is answered", () => {
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} answered="SQLite" />);
    expect(screen.queryByRole("button", { name: /Postgres/ })).toBeNull();
    expect(screen.getByText(/SQLite/)).toBeTruthy();
  });
});

describe("AskUserCard tabs", () => {
  /* Stacked, five questions are one long strip and the ones below the fold are
   * answered by nobody — the card sends `(未選擇)` for them without ever saying
   * so. Tabs put each question in its own place; what actually stops the miss
   * is that the tabs SAY which ones are still blank. */

  it("shows one question at a time, behind a tab each", () => {
    render(<AskUserCard call={twoQuestions} onAnswer={vi.fn()} />);

    expect(screen.getByText("Format?")).toBeTruthy();
    expect(screen.queryByText("Include charts?")).toBeNull();

    openTab(/Charts/);
    expect(screen.getByText("Include charts?")).toBeTruthy();
    expect(screen.queryByText("Format?")).toBeNull();
  });

  it("marks which tabs are still blank, and counts them on 送出", () => {
    // A tab hides its question, so hiding one is only safe if the strip says a
    // question is in there unanswered. The count on the button is the part that
    // cannot be misread — a dot alone never says what it wants.
    render(<AskUserCard call={twoQuestions} onAnswer={vi.fn()} />);

    expect(screen.getByRole("tab", { name: /Format.*未答/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Charts.*未答/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /送出.*2 題未答/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));

    expect(screen.getByRole("tab", { name: /^Format$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /送出.*1 題未答/ })).toBeTruthy();
  });

  it("keeps the blank marker's space once the question is answered", () => {
    // Dropping the dot narrows its tab, which re-flows a strip that wraps (it
    // does: the card is ~320px in the chat panel, and five headers do not fit
    // on one line). Answering one question would then shuffle the others out
    // from under the pointer. So the dot is hidden in place, never removed.
    render(<AskUserCard call={twoQuestions} onAnswer={vi.fn()} />);

    expect(screen.getAllByTestId("tab-blank-dot")).toHaveLength(2);
    expect(screen.getAllByTestId("tab-blank-dot")[0]).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));

    expect(screen.getAllByTestId("tab-blank-dot")).toHaveLength(2);
    expect(screen.getAllByTestId("tab-blank-dot")[0]).not.toBeVisible();
    expect(screen.getAllByTestId("tab-blank-dot")[1]).toBeVisible();
  });

  it("counts an answer in the user's own words as answered", () => {
    // The card offers three ways to answer and only one of them is an option.
    // A tab still marked blank after the user typed their answer into it would
    // be telling them they had not answered.
    render(<AskUserCard call={twoQuestions} onAnswer={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("自己回答"), { target: { value: "SVG" } });

    expect(screen.getByRole("tab", { name: /^Format$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /送出.*1 題未答/ })).toBeTruthy();
  });

  it("stays on the question just answered instead of flying to the next", () => {
    // Auto-advancing would be the stronger nudge, but every option carries its
    // own 補充 field: leaving on the click takes that field away before it can
    // be used, so the pick has to be able to sit still.
    render(<AskUserCard call={twoQuestions} onAnswer={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));

    expect(screen.getByText("Format?")).toBeTruthy();
    expect(screen.getByLabelText("補充:PDF")).toBeTruthy();
  });

  it("draws no tab strip for a single question", () => {
    render(<AskUserCard call={oneQuestion} onAnswer={vi.fn()} />);

    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    // And no count either — nothing is hidden, so there is nothing to warn about.
    expect(screen.getByRole("button", { name: /^送出$/ })).toBeTruthy();
  });

  it("keeps the tabs walkable after 送出, as the record of what went", () => {
    // #659's rule under tabs: only the button goes. If the strip stopped
    // switching, four of five answers would become unreachable at the very
    // moment they became the record of what was sent.
    render(<AskUserCard call={twoQuestions} onAnswer={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /PDF/ }));
    send();
    expect(screen.queryByRole("button", { name: /送出/ })).toBeNull();

    openTab(/Charts/);
    expect(screen.getByText("Include charts?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Yes/ })).toBeDisabled();

    openTab(/Format/);
    expect(screen.getByRole("button", { name: /PDF/ })).toHaveAttribute("aria-pressed", "true");
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
