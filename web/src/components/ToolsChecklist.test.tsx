// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { ToolsChecklist } from "./ToolsChecklist";

afterEach(cleanup);

const TOOLS: ItemToolState[] = [
  { key: "exec", group: "builtin", label: "Exec", description: "Run a shell command.", default_on: true, pref: "follow", effective: true },
  {
    key: "rca-tools",
    group: "rca-tools",
    label: "RCA Tools",
    description: "Bundled tools: Spc, Pareto.",
    default_on: true,
    pref: "off",
    effective: false,
  },
];

describe("ToolsChecklist", () => {
  it("renders one row per tool with its human label", () => {
    render(<ToolsChecklist tools={TOOLS} prefs={{}} onChange={vi.fn()} />);
    expect(screen.getByText("Exec")).toBeInTheDocument();
    expect(screen.getByText("RCA Tools")).toBeInTheDocument();
  });

  it("reflects the current tri-state: an absent key is Follow, false is Off", () => {
    render(<ToolsChecklist tools={TOOLS} prefs={{ "rca-tools": false }} onChange={vi.fn()} />);
    expect(screen.getByTestId("tool-exec-follow")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("tool-rca-tools-off")).toHaveAttribute("aria-pressed", "true");
  });

  it("forcing a follow tool On emits onChange with the key pinned true", () => {
    const onChange = vi.fn();
    render(<ToolsChecklist tools={TOOLS} prefs={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("tool-exec-on"));
    expect(onChange).toHaveBeenCalledWith({ exec: true });
  });

  it("setting a pinned tool back to Follow drops the key from the override", () => {
    const onChange = vi.fn();
    render(<ToolsChecklist tools={TOOLS} prefs={{ "rca-tools": false }} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("tool-rca-tools-follow"));
    expect(onChange).toHaveBeenCalledWith({});
  });

  it("exposes the full label + description via title= so a clipped row is readable on hover (#456)", () => {
    // Pinned off → the row shows the tool's own description (not the follow hint),
    // and both the label and that description are single-line clipped with ellipsis.
    render(<ToolsChecklist tools={TOOLS} prefs={{ "rca-tools": false }} onChange={vi.fn()} />);
    expect(screen.getByText("RCA Tools")).toHaveAttribute("title", "RCA Tools");
    expect(screen.getByText("Bundled tools: Spc, Pareto.")).toHaveAttribute(
      "title",
      "Bundled tools: Spc, Pareto.",
    );
  });

  // ── where a tool came from (#724) ──────────────────────────────────

  it("names the package a command-granularity row belongs to", () => {
    // `app.json` may grant a whole bundle or one command of it, so two rows
    // can look like peers while one is part of the other. Told only "Spc", a
    // reader cannot tell which row's switch governs the tool they saw in chat.
    render(
      <ToolsChecklist
        tools={[{ ...TOOLS[0], key: "rca-tools:spc", label: "Spc", package: "Rca Tools" }]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-rca-tools:spc");
    expect(row).toHaveTextContent("Rca Tools");
    expect(row).toHaveTextContent("Spc");
  });

  it("shows a third-party tool's release and author on its own row", () => {
    render(
      <ToolsChecklist
        tools={[
          {
            ...TOOLS[0],
            key: "wafer-history",
            group: "wafer-history",
            label: "Wafer History",
            external: true,
            version: "1.4.2",
            author: "Wafer Team <wafer@example.com>",
          },
        ]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-wafer-history");
    expect(row).toHaveTextContent("1.4.2");
    expect(row).toHaveTextContent("Wafer Team <wafer@example.com>");
  });

  it("says a first-party tool is the platform's own, rather than leaving it blank", () => {
    // Every row answers "who do I go to". A blank where other rows name a
    // person reads as missing information about the same kind of thing.
    render(<ToolsChecklist tools={[TOOLS[0]]} prefs={{}} onChange={vi.fn()} />);

    expect(screen.getByTestId("tool-row-exec")).toHaveTextContent("內建");
  });

  it("distinguishes a third-party tool whose author never filled their name in", () => {
    // NOT the same as "ours". Reading one off the absence of the other would
    // credit us with a stranger's code.
    render(
      <ToolsChecklist
        tools={[{ ...TOOLS[0], key: "wafer-history", external: true, version: "1.4.2" }]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-wafer-history");
    expect(row).toHaveTextContent("未註明作者");
    expect(row).not.toHaveTextContent("內建");
  });

  it("marks a row served from the cached copy", () => {
    render(
      <ToolsChecklist
        tools={[
          { ...TOOLS[0], key: "wafer-history", external: true, version: "1.4.2", stale: true },
        ]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByTestId("tool-wafer-history-stale")).toBeInTheDocument();
  });

  it("says why a declared tool could not be resolved, on the row that still has its switch", () => {
    render(
      <ToolsChecklist
        tools={[
          {
            ...TOOLS[0],
            key: "legacy-fetch",
            group: "legacy-fetch",
            external: true,
            unavailable: "404 — the artifact expired",
          },
        ]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-legacy-fetch");
    expect(row).toHaveTextContent("404 — the artifact expired");
    // And it claims nothing about a release or an author: nothing resolved,
    // so "no author published" would describe a manifest nobody read.
    expect(row).not.toHaveTextContent("未註明作者");
    expect(row).not.toHaveTextContent("內建");
  });
});

// plan-tools-picker-groups: the list folds by `group`. Three built-ins, two
// commands of one package, and one lone third-party package — enough to have
// a multi-row fold, a partially-granted package and a single-row fold.
const row = (over: Partial<ItemToolState> & Pick<ItemToolState, "key" | "group">): ItemToolState => ({
  label: over.key,
  description: "",
  default_on: true,
  pref: "follow",
  effective: true,
  ...over,
});
const GROUPED: ItemToolState[] = [
  row({ key: "exec", group: "builtin", label: "Exec" }),
  row({ key: "read_file", group: "builtin", label: "Read File" }),
  row({ key: "write_file", group: "builtin", label: "Write File" }),
  row({ key: "rca-tools:spc", group: "rca-tools", label: "Spc", package: "Rca Tools" }),
  row({ key: "rca-tools:pareto", group: "rca-tools", label: "Pareto", package: "Rca Tools" }),
  row({ key: "wafer-history", group: "wafer-history", label: "Wafer History", external: true }),
];

describe("ToolsChecklist folds rows by group", () => {
  it("draws one fold per multi-row group, builtin first and literally named builtin", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    const headers = screen.getAllByTestId(/^tool-group-header-/);
    expect(headers.map((h) => h.getAttribute("data-testid"))).toEqual([
      "tool-group-header-builtin",
      "tool-group-header-rca-tools",
    ]);
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveTextContent("builtin");
    expect(screen.getByTestId("tool-group-header-rca-tools")).toHaveTextContent("Rca Tools");
  });

  it("opens collapsed: a fold's rows appear only once its header is pressed", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    expect(screen.queryByTestId("tool-row-exec")).not.toBeInTheDocument();
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByTestId("tool-group-header-builtin"));
    expect(screen.getByTestId("tool-row-exec")).toBeInTheDocument();
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "true");
  });

  it("a mixed fold opens by itself, presses none of its three states and says mixed", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{ exec: true }} onChange={vi.fn()} />);
    expect(screen.getByTestId("tool-row-exec")).toBeInTheDocument(); // auto-expanded
    expect(screen.getByTestId("tool-group-builtin-mixed")).toBeInTheDocument();
    for (const opt of ["follow", "on", "off"]) {
      expect(screen.getByTestId(`tool-group-builtin-${opt}`)).toHaveAttribute("aria-pressed", "false");
    }
    // the other fold is untouched, so it stays shut
    expect(screen.queryByTestId("tool-row-rca-tools:spc")).not.toBeInTheDocument();
  });

  it("a fold's state applies to every row in it, and follow clears them all", () => {
    const onChange = vi.fn();
    render(<ToolsChecklist tools={GROUPED} prefs={{ exec: false }} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("tool-group-rca-tools-on"));
    expect(onChange).toHaveBeenLastCalledWith({ exec: false, "rca-tools:spc": true, "rca-tools:pareto": true });

    cleanup();
    render(
      <ToolsChecklist
        tools={GROUPED}
        prefs={{ exec: false, "rca-tools:spc": true, "rca-tools:pareto": true }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("tool-group-rca-tools-follow"));
    expect(onChange).toHaveBeenLastCalledWith({ exec: false });
  });

  it("a single-row group is just its row — no fold to open", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    expect(screen.queryByTestId("tool-group-header-wafer-history")).not.toBeInTheDocument();
    expect(screen.getByTestId("tool-row-wafer-history")).toBeInTheDocument();
    expect(screen.getByTestId("tool-wafer-history-follow")).toHaveAttribute("aria-pressed", "true");
  });

  it("a search hides folds with no match and opens a matching fold on just its matches", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "spc" } });
    expect(screen.queryByTestId("tool-group-header-builtin")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tool-row-wafer-history")).not.toBeInTheDocument();
    expect(screen.getByTestId("tool-row-rca-tools:spc")).toBeInTheDocument();
    expect(screen.queryByTestId("tool-row-rca-tools:pareto")).not.toBeInTheDocument();

    // the fold's own name matches too, and then every row of it shows
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "builtin" } });
    expect(screen.getByTestId("tool-row-exec")).toBeInTheDocument();
    expect(screen.getByTestId("tool-row-write_file")).toBeInTheDocument();
    expect(screen.queryByTestId("tool-group-header-rca-tools")).not.toBeInTheDocument();
  });

  it("a partially granted package reads On once every granted row is on — the ceiling is the whole", () => {
    // rca-tools has more commands than the two the app granted; the fold must
    // never look for the ones that are not here.
    render(
      <ToolsChecklist
        tools={GROUPED}
        prefs={{ "rca-tools:spc": true, "rca-tools:pareto": true }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tool-group-rca-tools-on")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("tool-group-rca-tools-mixed")).not.toBeInTheDocument();
    expect(screen.getByTestId("tool-group-header-rca-tools")).toHaveTextContent("2");
  });

  it("reset to defaults still clears every row the modal governs, folded or not", () => {
    const onChange = vi.fn();
    render(
      <ToolsChecklist tools={GROUPED} prefs={{ exec: false, "rca-tools:spc": true }} onChange={onChange} />,
    );
    fireEvent.click(screen.getByTestId("tools-reset"));
    expect(onChange).toHaveBeenLastCalledWith({});
  });
});
