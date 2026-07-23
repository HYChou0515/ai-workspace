// @vitest-environment happy-dom
/** #613 P2: the pinned todo checklist next to the chat — hydrates via GET,
 * user edits whole-list-PUT when no turn is streaming, locks while one is. */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../test/queryWrapper";
import type { ChatGoal, GoalRead, ItemGoalApi } from "../api/itemGoal";
import type { ItemTodosApi, TodoItem } from "../api/itemTodos";
import { TodoPanel } from "./TodoPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fakeApi(initial: TodoItem[]): ItemTodosApi & { puts: TodoItem[][] } {
  const puts: TodoItem[][] = [];
  return {
    puts,
    getTodos: vi.fn(async () => initial),
    putTodos: vi.fn(async (_s: string, _i: string, _c: string, items: TodoItem[]) => {
      puts.push(items);
      return items;
    }),
  };
}

function fakeGoalApi(
  initial: ChatGoal | null,
  checkerEnabled = true,
): ItemGoalApi & { puts: string[]; deletes: number } {
  const state: GoalRead = { goal: initial, checker_enabled: checkerEnabled };
  const api = {
    puts: [] as string[],
    deletes: 0,
    getGoal: vi.fn(async () => state),
    putGoal: vi.fn(async (_s: string, _i: string, _c: string, condition: string) => {
      api.puts.push(condition);
      return {
        goal: {
          condition,
          set_by: "me",
          rounds_used: 0,
          state: "active" as const,
          max_rounds: 3,
        },
        checker_enabled: checkerEnabled,
      };
    }),
    deleteGoal: vi.fn(async () => {
      api.deletes += 1;
    }),
  };
  return api;
}

const mount = (api: ItemTodosApi, props: Partial<Parameters<typeof TodoPanel>[0]> = {}) =>
  renderWithQuery(
    <TodoPanel
      slug="rca"
      itemId="i1"
      chatId="c1"
      streaming={false}
      readOnly={false}
      client={api}
      goalClient={props.goalClient ?? fakeGoalApi(null)}
      {...props}
    />,
  );

describe("TodoPanel", () => {
  it("renders the fetched checklist with statuses", async () => {
    mount(
      fakeApi([
        { text: "read logs", status: "completed" },
        { text: "fix bug", status: "in_progress" },
        { text: "run suite", status: "pending" },
      ]),
    );
    expect(await screen.findByText("fix bug")).toBeInTheDocument();
    expect(screen.getByText("read logs")).toBeInTheDocument();
    // The completed item's checkbox is checked; the pending one's is not.
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0]).toBeChecked();
    expect(boxes[2]).not.toBeChecked();
  });

  it("toggling a checkbox PUTs the whole list with the item flipped", async () => {
    const api = fakeApi([
      { text: "a", status: "pending" },
      { text: "b", status: "completed" },
    ]);
    mount(api);
    fireEvent.click((await screen.findAllByRole("checkbox"))[0]);
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual([
      { text: "a", status: "completed" },
      { text: "b", status: "completed" },
    ]);
  });

  it("locks editing while a turn is streaming", async () => {
    mount(fakeApi([{ text: "a", status: "pending" }]), { streaming: true });
    const box = (await screen.findAllByRole("checkbox"))[0];
    expect(box).toBeDisabled();
  });

  it("adds a new pending item via the input", async () => {
    const api = fakeApi([{ text: "a", status: "pending" }]);
    mount(api);
    await screen.findByText("a");
    fireEvent.change(screen.getByTestId("todo-add-input"), { target: { value: "new step" } });
    fireEvent.click(screen.getByTestId("todo-add"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual([
      { text: "a", status: "pending" },
      { text: "new step", status: "pending" },
    ]);
  });

  it("sets a goal from the input", async () => {
    const goalApi = fakeGoalApi(null);
    mount(fakeApi([{ text: "a", status: "pending" }]), { goalClient: goalApi });
    await screen.findByText("a");
    fireEvent.change(screen.getByTestId("goal-input"), {
      target: { value: "the report exists" },
    });
    fireEvent.click(screen.getByTestId("goal-set"));
    await waitFor(() => expect(goalApi.puts).toEqual(["the report exists"]));
    // The saved goal replaces the input with the active-goal row.
    expect(await screen.findByTestId("goal-row")).toHaveTextContent("the report exists");
    expect(screen.getByTestId("goal-rounds")).toBeInTheDocument();
  });

  it("shows an active goal with its round budget and clears it", async () => {
    const goalApi = fakeGoalApi({
      condition: "tests pass",
      set_by: "me",
      rounds_used: 1,
      state: "active",
      max_rounds: 3,
    });
    mount(fakeApi([]), { goalClient: goalApi });
    expect(await screen.findByTestId("goal-row")).toHaveTextContent("tests pass");
    fireEvent.click(screen.getByTestId("goal-clear"));
    await waitFor(() => expect(goalApi.deletes).toBe(1));
  });

  it("shows the met state", async () => {
    const goalApi = fakeGoalApi({
      condition: "done",
      set_by: "me",
      rounds_used: 2,
      state: "met",
      max_rounds: 3,
    });
    mount(fakeApi([]), { goalClient: goalApi });
    expect(await screen.findByTestId("goal-met")).toBeInTheDocument();
  });

  it("warns when the deploy has no goal checker", async () => {
    const goalApi = fakeGoalApi(
      { condition: "c", set_by: "me", rounds_used: 0, state: "active", max_rounds: 3 },
      false,
    );
    mount(fakeApi([]), { goalClient: goalApi });
    expect(await screen.findByTestId("goal-no-checker")).toBeInTheDocument();
  });

  it("locks the goal controls while a turn is streaming", async () => {
    const goalApi = fakeGoalApi({
      condition: "c",
      set_by: "me",
      rounds_used: 0,
      state: "active",
      max_rounds: 3,
    });
    mount(fakeApi([{ text: "a", status: "pending" }]), {
      goalClient: goalApi,
      streaming: true,
    });
    expect(await screen.findByTestId("goal-clear")).toBeDisabled();
  });

  it("removes an item via its delete button", async () => {
    const api = fakeApi([
      { text: "a", status: "pending" },
      { text: "b", status: "pending" },
    ]);
    mount(api);
    await screen.findByText("a");
    fireEvent.click(screen.getAllByTestId("todo-remove")[0]);
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual([{ text: "b", status: "pending" }]);
  });
});
