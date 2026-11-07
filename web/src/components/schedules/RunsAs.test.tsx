// @vitest-environment happy-dom
/**
 * Who a schedule runs as, and why (`docs/plan-schedule-run-as.md` G8) — ONE
 * control, drawn the same way on the schedules overview, in the item's
 * Workflows panel and in a page's own schedules list (G9, NN/g consistency).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  api: {
    getUsers: vi.fn(async () => [
      { id: "carol", name: "Carol", section: "QA", email: "carol@x" },
      { id: "dave", name: "Dave", section: "QA", email: "dave@x" },
    ]),
  },
}));

// The item's people (plan-people-picker-ranking D4) — nobody unless a test says.
let itemPeople: string[] = [];
const useItemPeopleCalls: Array<[string, string]> = [];
vi.mock("../../hooks/useItemPeople", () => ({
  useItemPeople: (slug: string, itemId: string) => {
    useItemPeopleCalls.push([slug, itemId]);
    return itemPeople;
  },
}));

import type { ScheduleBindingsClient } from "../../api/scheduleBindings";
import type { RunsAsReason } from "../../api/schedules";
import { translate } from "../../lib/i18n";
import { renderWithQuery } from "../../test/queryWrapper";
import { RunsAs } from "./RunsAs";

afterEach(cleanup);

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

function bindings(): ScheduleBindingsClient {
  return {
    list: vi.fn(async () => []),
    bind: vi.fn(async () => {}),
    unbind: vi.fn(async () => {}),
    assign: vi.fn(async () => {}),
  };
}

function draw({
  who = "",
  reason = "" as RunsAsReason,
  by = "",
  mine = false,
  canRun = true,
  canAssign = false,
  client = bindings(),
} = {}) {
  const onChanged = vi.fn();
  renderWithQuery(
    <RunsAs
      slug="rca"
      itemId="i-1"
      path="/.workflows/schedules.json"
      triggerId="k-1"
      runsAs={{ who, reason, by, mine }}
      canRun={canRun}
      canAssign={canAssign}
      client={client}
      onChanged={onChanged}
    />,
  );
  return { client, onChanged };
}

describe("what it says", () => {
  it("names the author as the reason it runs as them", () => {
    draw({ who: "bob", reason: "author" });
    expect(screen.getByTestId("runs-as")).toHaveTextContent(
      word("sched.runsAsWhy", { main: word("sched.runsAs", { who: "bob" }), why: word("sched.why.author") }),
    );
  });

  it("says it is you, and that you chose it", () => {
    draw({ who: "me", reason: "self", mine: true });
    expect(screen.getByTestId("runs-as")).toHaveTextContent(
      word("sched.runsAsWhy", { main: word("sched.runsAsYou"), why: word("sched.why.selfYou") }),
    );
  });

  it("says it is you when you wrote it", () => {
    draw({ who: "me", reason: "author", mine: true });
    expect(screen.getByTestId("runs-as")).toHaveTextContent(word("sched.why.authorYou"));
  });

  it("says they changed the workflow when that is why it runs as them", () => {
    // plan-seam-token-refresh W1: they rewrote what the schedule runs — saying
    // "wrote this schedule" would state a reason that is not true.
    draw({ who: "bob", reason: "workflow" });
    expect(screen.getByTestId("runs-as")).toHaveTextContent(word("sched.why.workflow"));
    expect(screen.getByTestId("runs-as")).not.toHaveTextContent(word("sched.why.author"));
  });

  it("says it is you when you changed the workflow", () => {
    draw({ who: "me", reason: "workflow", mine: true });
    expect(screen.getByTestId("runs-as")).toHaveTextContent(word("sched.why.workflowYou"));
  });

  it("names the administrator who chose the person", () => {
    draw({ who: "carol", reason: "admin", by: "root" });
    expect(screen.getByTestId("runs-as")).toHaveTextContent(word("sched.why.admin", { by: "root" }));
  });

  it("says plainly that nobody's own values are used", () => {
    draw();
    expect(screen.getByTestId("runs-as")).toHaveTextContent(word("sched.runsAsNobody"));
  });
});

describe("what it lets you do", () => {
  it("runs a schedule nobody holds as you, at once", async () => {
    const { client, onChanged } = draw();
    fireEvent.click(screen.getByRole("button", { name: word("sched.bindMe") }));
    await waitFor(() =>
      expect(client.bind).toHaveBeenCalledWith("rca", "i-1", "/.workflows/schedules.json", "k-1"),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("asks before taking a schedule over from someone", async () => {
    const { client } = draw({ who: "bob", reason: "author" });
    fireEvent.click(screen.getByRole("button", { name: word("sched.replaceMe") }));
    const dialog = await screen.findByRole("dialog");
    expect(client.bind).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: word("sched.replaceMe") }));
    await waitFor(() => expect(client.bind).toHaveBeenCalled());
  });

  it("lets the person it runs as stop it", async () => {
    const { client } = draw({ who: "me", reason: "admin", by: "root", mine: true });
    fireEvent.click(screen.getByRole("button", { name: word("sched.unbind") }));
    await waitFor(() => expect(client.unbind).toHaveBeenCalled());
  });

  it("offers nothing to someone who may not run work in the item", () => {
    draw({ who: "bob", reason: "author", canRun: false });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("looks the item's people up only once someone chooses who it runs as", async () => {
    // Every row of the schedules overview draws a RunsAs: asking for each
    // row's item on page load made one request per item for a picker nobody
    // had opened (review round 1).
    useItemPeopleCalls.length = 0;
    draw({ who: "bob", reason: "author", canAssign: true });
    expect(useItemPeopleCalls).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: word("sched.assign") }));
    await screen.findByRole("dialog");
    expect(useItemPeopleCalls.length).toBeGreaterThan(0);
  });

  it("lists the item's own people first when choosing who it runs as", async () => {
    // By name Carol comes first; Dave belongs to this item, so he leads.
    itemPeople = ["dave"];
    try {
      draw({ who: "bob", reason: "author", canAssign: true });
      fireEvent.click(screen.getByRole("button", { name: word("sched.assign") }));
      const dialog = await screen.findByRole("dialog");
      await within(dialog).findByText("Carol");
      const names = within(dialog)
        .getAllByRole("listitem")
        .map((li) => li.querySelector("[data-person-name]")?.textContent)
        .filter(Boolean);
      expect(names).toEqual(["Dave", "Carol"]);
    } finally {
      itemPeople = [];
    }
  });

  it("lets an administrator choose someone else", async () => {
    const { client } = draw({ who: "bob", reason: "author", canAssign: true });
    fireEvent.click(screen.getByRole("button", { name: word("sched.assign") }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(await within(dialog).findByText("Carol"));
    await waitFor(() =>
      expect(client.assign).toHaveBeenCalledWith("rca", "i-1", "/.workflows/schedules.json", "k-1", "carol"),
    );
  });

  it("says why a change was refused", async () => {
    const client = bindings();
    client.bind = vi.fn(async () => {
      throw Object.assign(new Error("x"), { detail: "nope" });
    });
    const { onChanged } = draw({ client });
    fireEvent.click(screen.getByRole("button", { name: word("sched.bindMe") }));
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
    // Refused because the row changed meanwhile: the list is refetched, so it
    // shows what IS there (review round 1).
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});
