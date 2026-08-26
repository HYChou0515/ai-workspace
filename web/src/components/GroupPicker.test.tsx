// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PickableGroup } from "../api/groups";
import { GroupPicker } from "./GroupPicker";

const groups: PickableGroup[] = [
  { resource_id: "g:reflow", name: "Reflow", description: "Solder reflow line", member_count: 4 },
  { resource_id: "g:ae", name: "Applications Engineering", description: "Field support", member_count: 12 },
  { resource_id: "g:qa", name: "quality assurance", description: "Inspection", member_count: 7 },
  { resource_id: "g:mfg", name: "Manufacturing", description: "Reflow + assembly", member_count: 30 },
];

afterEach(cleanup);

// queryAll, not getAll: the empty case is one of the things under test, and
// getAllByTestId throws rather than returning [].
const names = () =>
  screen.queryAllByTestId(/^group-picker-item-/).map((el) => el.getAttribute("data-group-name"));

describe("GroupPicker", () => {
  it("lists groups by name, not in whatever order the API returned", () => {
    // The endpoint returns store order, so the picker was effectively unordered —
    // with more than a handful of groups you cannot find yours by eye.
    render(<GroupPicker groups={groups} onPick={vi.fn()} />);

    expect(names()).toEqual([
      "Applications Engineering",
      "Manufacturing",
      "quality assurance",
      "Reflow",
    ]);
  });

  it("sorts case-insensitively, so a lowercase name is not exiled to the end", () => {
    // A plain `<` comparison puts every lowercase name after every uppercase one,
    // which reads as "unsorted" to the person looking for it.
    render(<GroupPicker groups={groups} onPick={vi.fn()} />);

    expect(names()!.indexOf("quality assurance")).toBeLessThan(names()!.indexOf("Reflow"));
  });

  it("filters as you type", () => {
    render(<GroupPicker groups={groups} onPick={vi.fn()} />);

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "manu" } });
    expect(names()).toEqual(["Manufacturing"]);
  });

  it("searches the description too — people remember what a group is for", () => {
    render(<GroupPicker groups={groups} onPick={vi.fn()} />);

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "inspection" } });
    expect(names()).toEqual(["quality assurance"]);
  });

  it("says so when nothing matches, rather than showing an empty box", () => {
    render(<GroupPicker groups={groups} onPick={vi.fn()} />);

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
    expect(names()).toEqual([]);
    expect(screen.getByText(/no matches/i)).toBeInTheDocument();
  });

  it("hands back the id that was picked", () => {
    const onPick = vi.fn();
    render(<GroupPicker groups={groups} onPick={onPick} />);

    fireEvent.click(screen.getByTestId("group-picker-item-g:reflow"));
    expect(onPick).toHaveBeenCalledWith("g:reflow");
  });

  it("leaves out groups already granted", () => {
    render(<GroupPicker groups={groups} onPick={vi.fn()} exclude={["g:mfg", "g:ae"]} />);

    expect(names()).toEqual(["quality assurance", "Reflow"]);
  });
});
