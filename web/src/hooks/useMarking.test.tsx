// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkingStore } from "../lib/markings";
import { MarkingProvider, useMarking, useMarkingNames } from "./useMarking";

afterEach(cleanup);

const renders: Record<string, number> = {};

function View({ name }: { name: string | null }) {
  const [entry] = useMarking(name);
  renders[name ?? "none"] = (renders[name ?? "none"] ?? 0) + 1;
  const lots = entry ? [...(entry.marking.lot ?? [])].join(",") : "-";
  return <span data-testid={`view-${name ?? "none"}`}>{lots}</span>;
}

function Writer({ name }: { name: string }) {
  const [, write] = useMarking(name);
  return (
    <button type="button" onClick={() => write({ lot: new Set(["L1"]) }, "/v/grid.ai.yaml")}>
      write {name}
    </button>
  );
}

function Names() {
  return <span data-testid="names">{useMarkingNames().join(",")}</span>;
}

describe("useMarking", () => {
  it("every view on a marking sees a write; a view on another does not re-render", () => {
    const store = new MarkingStore();
    for (const k of Object.keys(renders)) delete renders[k];
    render(
      <MarkingProvider store={store}>
        <View name="fail" />
        <View name="other" />
        <View name={null} />
        <Writer name="fail" />
        <Names />
      </MarkingProvider>,
    );
    const before = { other: renders.other, none: renders.none };

    act(() => screen.getByRole("button", { name: "write fail" }).click());

    expect(screen.getByTestId("view-fail")).toHaveTextContent("L1");
    expect(screen.getByTestId("view-other")).toHaveTextContent("-");
    expect(renders.other).toBe(before.other);
    expect(renders.none).toBe(before.none);
    expect(screen.getByTestId("names")).toHaveTextContent("fail");
    expect(store.get("fail")?.source).toBe("/v/grid.ai.yaml");
  });

  it("a detached view (no name) reads nothing and its write is a no-op", () => {
    const store = new MarkingStore();
    let write: ReturnType<typeof useMarking>[1] | null = null;
    function Detached() {
      const [entry, w] = useMarking(null);
      write = w;
      return <span data-testid="d">{entry ? "lit" : "dark"}</span>;
    }
    render(
      <MarkingProvider store={store}>
        <Detached />
      </MarkingProvider>,
    );
    act(() => write!({ lot: new Set(["L1"]) }, null));
    expect(screen.getByTestId("d")).toHaveTextContent("dark");
    expect(store.names()).toEqual([]);
  });

  it("outside a provider, reads nothing and writes nowhere rather than throwing", () => {
    render(<View name="fail" />);
    expect(screen.getByTestId("view-fail")).toHaveTextContent("-");
  });

  it("a write says whether a store took it: only a view on a marking in a provider's (#847/#848 PR 5 P40 row 19)", () => {
    const store = new MarkingStore();
    const took: Record<string, boolean> = {};
    function Probe({ id, name }: { id: string; name: string | null }) {
      const [, write] = useMarking(name);
      return (
        <button type="button" onClick={() => (took[id] = write({ lot: new Set(["L1"]) }, null))}>
          {id}
        </button>
      );
    }
    render(
      <>
        <MarkingProvider store={store}>
          <Probe id="on" name="m" />
          <Probe id="detached" name={null} />
        </MarkingProvider>
        <Probe id="bare" name="m" />
      </>,
    );
    for (const id of ["on", "detached", "bare"]) act(() => screen.getByRole("button", { name: id }).click());
    expect(took).toEqual({ on: true, detached: false, bare: false });
  });
});
