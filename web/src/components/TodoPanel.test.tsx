// @vitest-environment happy-dom
/** #613 P2: the pinned todo checklist next to the chat — hydrates via GET,
 * user edits whole-list-PUT when no turn is streaming, locks while one is. */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../test/queryWrapper";
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

const mount = (api: ItemTodosApi, props: Partial<Parameters<typeof TodoPanel>[0]> = {}) =>
  renderWithQuery(
    <TodoPanel slug="rca" itemId="i1" chatId="c1" streaming={false} readOnly={false} client={api} {...props} />,
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
