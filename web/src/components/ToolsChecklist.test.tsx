// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
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
        tools={[{ ...TOOLS[0], key: "rca-tools:spc", group: "rca-tools", label: "Spc", package: "Rca Tools" }]}
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
        tools={[{ ...TOOLS[0], key: "wafer-history", group: "wafer-history", external: true, version: "1.4.2" }]}
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
          { ...TOOLS[0], key: "wafer-history", group: "wafer-history", external: true, version: "1.4.2", stale: true },
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
  it("draws one fold per multi-row group, the core fold first, named for what it is", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    const headers = screen.getAllByTestId(/^tool-group-header-/);
    expect(headers.map((h) => h.getAttribute("data-testid"))).toEqual([
      "tool-group-header-builtin",
      "tool-group-header-rca-tools",
    ]);
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveTextContent("核心工具");
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
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "核心" } });
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

// Review round 1 (plan P6): every rule the first round found un-pinned, and
// the two open/close rules it changed. A stateful host, because several of
// these are about what happens AFTER a change lands in `prefs`.
function Host({ tools, initial }: { tools: ItemToolState[]; initial: Record<string, boolean> }) {
  const [prefs, setPrefs] = useState(initial);
  return <ToolsChecklist tools={tools} prefs={prefs} onChange={setPrefs} />;
}

describe("ToolsChecklist folds — rules pinned after review", () => {
  it("puts the core fold first even when the rows arrive package-first", () => {
    const packageFirst = [GROUPED[3]!, GROUPED[4]!, GROUPED[5]!, GROUPED[0]!, GROUPED[1]!, GROUPED[2]!];
    render(<ToolsChecklist tools={packageFirst} prefs={{}} onChange={vi.fn()} />);
    const headers = screen.getAllByTestId(/^tool-group-header-/);
    expect(headers[0]).toHaveAttribute("data-testid", "tool-group-header-builtin");
  });

  it("a row inside an opened fold is still its own switch", () => {
    const onChange = vi.fn();
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("tool-group-header-builtin"));
    fireEvent.click(screen.getByTestId("tool-read_file-off"));
    expect(onChange).toHaveBeenLastCalledWith({ read_file: false });
  });

  it("reset clears the rows of a fold that is collapsed and uniform", () => {
    const onChange = vi.fn();
    render(
      <ToolsChecklist tools={GROUPED} prefs={{ exec: false, read_file: false, write_file: false }} onChange={onChange} />,
    );
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByTestId("tools-reset"));
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("a fold that opened because it was mixed stays open when a row inside makes it uniform", () => {
    render(<Host tools={GROUPED} initial={{ exec: true }} />);
    expect(screen.getByTestId("tool-row-exec")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tool-exec-follow")); // now uniform
    expect(screen.getByTestId("tool-row-exec")).toBeInTheDocument();
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "true");
    // and pressing the fold's own state does not shut it either
    fireEvent.click(screen.getByTestId("tool-group-builtin-on"));
    expect(screen.getByTestId("tool-row-write_file")).toBeInTheDocument();
  });

  it("a new search term overrides a fold the reader had closed by hand", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    fireEvent.click(screen.getByTestId("tool-group-header-builtin")); // open
    fireEvent.click(screen.getByTestId("tool-group-header-builtin")); // close by hand
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "read" } });
    expect(screen.getByTestId("tool-row-read_file")).toBeInTheDocument();
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "true");
  });

  it("under a search a fold IS its matching rows: count, state and its tri-state cover only them", () => {
    const onChange = vi.fn();
    render(
      <ToolsChecklist tools={GROUPED} prefs={{ "rca-tools:pareto": false }} onChange={onChange} />,
    );
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "spc" } });
    const header = screen.getByTestId("tool-group-header-rca-tools");
    expect(header).toHaveTextContent("1");
    expect(header).not.toHaveTextContent("2");
    // the hidden pareto row is Off, the shown spc row is Follow: the fold reads Follow, not mixed
    expect(screen.getByTestId("tool-group-rca-tools-follow")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("tool-group-rca-tools-mixed")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tool-group-rca-tools-on"));
    expect(onChange).toHaveBeenLastCalledWith({ "rca-tools:pareto": false, "rca-tools:spc": true });
  });

  it("names the fold for assistive tech by what is on it, and says mixed on the tri-state", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{ exec: true }} onChange={vi.fn()} />);
    // the header button's accessible name is its visible text — name, count, mixed
    expect(screen.getByRole("button", { name: /核心工具.*3.*混合/ })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /核心工具.*混合/ })).toBeInTheDocument();
  });

  it("rows an older server sent without a group fall back to the flat list", () => {
    const legacy = GROUPED.map((t) => {
      const { group: _g, ...rest } = t;
      return rest as ItemToolState;
    });
    render(<ToolsChecklist tools={legacy} prefs={{}} onChange={vi.fn()} />);
    expect(screen.queryAllByTestId(/^tool-group-header-/)).toHaveLength(0);
    for (const t of GROUPED) expect(screen.getByTestId(`tool-row-${t.key}`)).toBeInTheDocument();
  });
});

// Review round 2 (plan P7): the open/shut state table, event by event.
describe("ToolsChecklist folds — what a search change restores", () => {
  it("a fold opened by hand survives a search typed and erased", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    fireEvent.click(screen.getByTestId("tool-group-header-builtin")); // hand-open
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "r" } });
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "" } });
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("tool-row-exec")).toBeInTheDocument();
  });

  it("a fold made mixed during a search stays open once the search is cleared", () => {
    render(<Host tools={GROUPED} initial={{}} />);
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "read" } });
    fireEvent.click(screen.getByTestId("tool-read_file-off")); // the fold is mixed now
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "" } });
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("tool-row-read_file")).toBeInTheDocument();
  });

  it("reset under a search clears only the rows the search left in", () => {
    const onChange = vi.fn();
    render(
      <ToolsChecklist
        tools={GROUPED}
        prefs={{ exec: false, read_file: false, "rca-tools:pareto": true }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "read" } });
    fireEvent.click(screen.getByTestId("tools-reset"));
    expect(onChange).toHaveBeenLastCalledWith({ exec: false, "rca-tools:pareto": true });
  });

  it("a search that only changed by whitespace releases nothing", () => {
    render(<ToolsChecklist tools={GROUPED} prefs={{}} onChange={vi.fn()} />);
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "核心" } });
    fireEvent.click(screen.getByTestId("tool-group-header-builtin")); // hand-shut under the search
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "核心 " } });
    expect(screen.getByTestId("tool-group-header-builtin")).toHaveAttribute("aria-expanded", "false");
  });
});
