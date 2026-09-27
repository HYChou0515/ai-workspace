// @vitest-environment happy-dom
/**
 * #847 P7: the markings sent with a message, as chips — above the composer
 * (removable) and on the message in the thread (with a refused one's reason).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SentMarking } from "../api/types";
import { OpenFileProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import { LocaleProvider, setStoredLocale } from "../lib/i18n";
import { MarkingChips } from "./MarkingChips";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const written: SentMarking = {
  name: "fail",
  path: "/.markings/fail.json",
  count: 4,
  keys: ["lot", "wafer"],
  error: null,
};
const refused: SentMarking = {
  name: "big",
  path: "",
  count: 9,
  keys: ["lot"],
  error: "workspace is full: 100 of 100 bytes used, and this write does not fit",
};

describe("MarkingChips", () => {
  it("names each marking, how many rows were picked, and the keys (#861 D5)", () => {
    render(<MarkingChips markings={[written]} />);
    const chip = screen.getByTestId("marking-chip");
    expect(chip).toHaveTextContent("fail");
    // Not "lot (2), wafer (12)": the picks are counted, not each column's values.
    expect(chip).toHaveTextContent("· 4 · 依 lot、wafer");
  });

  it("a chip sent before #861 (it reads back with no keys) shows its name, not a count of 0", () => {
    // msgspec drops #855's `counts` and defaults `count` to 0, `keys` to [].
    render(<MarkingChips markings={[{ name: "old", path: "/.markings/old.json", count: 0, keys: [] }]} />);
    const chip = screen.getByTestId("marking-chip");
    expect(chip).toHaveTextContent(/^old$/);
  });

  it("says the count and the keys in the viewer's language (#861 D5)", () => {
    setStoredLocale("en");
    render(
      <LocaleProvider>
        <MarkingChips markings={[written]} />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("marking-chip")).toHaveTextContent(/^fail\s*· 4 · by lot, wafer$/);
  });

  it("names them in Chinese too", () => {
    setStoredLocale("zh-TW");
    render(
      <LocaleProvider>
        <MarkingChips markings={[written]} />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("marking-chip")).toHaveTextContent(/^fail\s*· 4 · 依 lot、wafer$/);
  });

  it("says why a refused one was not sent", () => {
    render(<MarkingChips markings={[refused]} />);
    const chip = screen.getByTestId("marking-chip");
    expect(chip).toHaveAttribute("data-refused", "true");
    expect(chip).toHaveTextContent("workspace is full");
  });

  it("offers a remove control per chip when the composer asks for one", () => {
    const onRemove = vi.fn();
    render(<MarkingChips markings={[written, refused]} onRemove={onRemove} />);
    // No shell here, so the only buttons are the remove controls.
    const removes = screen.getAllByRole("button");
    expect(removes[1]).toHaveAccessibleName(/big/);
    expect(removes).toHaveLength(2);
    fireEvent.click(removes[1]!);
    expect(onRemove).toHaveBeenCalledWith("big");
  });

  it("opens the written file in the workspace when there is one on screen", () => {
    const openFile = vi.fn();
    render(
      <OpenFileProvider value={openFile}>
        <WorkspaceVisibleProvider value>
          <MarkingChips markings={[written, refused]} />
        </WorkspaceVisibleProvider>
      </OpenFileProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /fail/ }));
    expect(openFile).toHaveBeenCalledWith("/.markings/fail.json");
    // A refused one has no file to open.
    expect(screen.queryByRole("button", { name: /big/ })).not.toBeInTheDocument();
  });

  it("renders nothing for no markings", () => {
    const { container } = render(<MarkingChips markings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
