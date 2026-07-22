// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileInfo } from "../../api/types";
import { CommandPalette } from "./CommandPalette";

afterEach(cleanup);

function open(files: FileInfo[], onPick = vi.fn()) {
  render(<CommandPalette open files={files} onClose={vi.fn()} onPick={onPick} />);
  return { input: screen.getByPlaceholderText("Go to file…"), onPick };
}

/** Each result row is a button whose text carries the file's full path. */
function rows(): string[] {
  return screen.getAllByRole("button").map((b) => b.textContent ?? "");
}

const files: FileInfo[] = [
  { path: "/docs/wafer_map.csv", size: 1 },
  { path: "/map.csv", size: 1 },
  { path: "/readme.md", size: 1 },
];

describe("CommandPalette — fuzzy file matching", () => {
  it("finds a file by non-contiguous characters, not just a substring", () => {
    const { input } = open(files);
    fireEvent.change(input, { target: { value: "wafmap" } }); // not a substring of any path
    const out = rows();
    expect(out.some((r) => r.includes("/docs/wafer_map.csv"))).toBe(true);
    expect(out.some((r) => r.includes("readme"))).toBe(false);
  });

  it("ranks the tighter match first", () => {
    const { input } = open(files);
    fireEvent.change(input, { target: { value: "map" } });
    const out = rows();
    expect(out[0]).toContain("/map.csv");
    expect(out[0]).not.toContain("wafer");
  });

  // The whole reason the user raised this: a path can carry U+2215 (`∕`) where an
  // ASCII `/` can't go, and nobody can type `∕`. A typed `/` must find it.
  it("matches a typed / against the U+2215 slash look-alike in a path", () => {
    const { input } = open([{ path: "col-1∕me∕guide.md", size: 1 }]);
    fireEvent.change(input, { target: { value: "me/guide" } });
    expect(rows().length).toBe(1);
  });
});
