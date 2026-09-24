// @vitest-environment happy-dom
/**
 * The SPA's runtime view-plugin loader (#847/#848 PR1 P4).
 *
 * The loader never blocks the app: whatever goes wrong with one plugin — a list
 * that won't load, an SDK major that differs, an `import()` that throws or
 * hangs, a module that registers nothing — the app still renders, and every
 * `*.ai.yaml` naming that plugin's kinds shows a loud per-panel error naming
 * the plugin and the reason, through the view panel's own error boundary.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_PREFIX } from "../api/http";
import { ViewErrorBoundary } from "../renderers/entity/ViewErrorBoundary";
import type { EntityViewProps, ViewSpec } from "../renderers/entity/types";
import { registerViewKind, resolveViewRenderer, unregisterViewKind } from "../renderers/entity/viewKindRegistry";
import { SDK_VERSION } from "./sdkVersion";
import { loadViewPlugins, type ViewPluginInfo } from "./loader";

const touched = new Set<string>();
afterEach(() => {
  cleanup();
  for (const k of touched) unregisterViewKind(k);
  touched.clear();
  vi.useRealTimers();
});

function info(name: string, kinds: string[], sdk = SDK_VERSION): ViewPluginInfo {
  for (const k of kinds) touched.add(k);
  return { name, sdk, kinds, entry_url: `/view-plugins/${name}/index.js` };
}

/** Render a kind the way the panel does: inside the view error boundary. */
function renderKind(kind: string) {
  const { Component } = resolveViewRenderer(kind);
  const C = Component as ComponentType<EntityViewProps>;
  const spec = { view: kind } as ViewSpec;
  return render(
    <ViewErrorBoundary kind={kind} resetKey={kind}>
      <C spec={spec} type={null} entities={[]} onCreate={() => {}} onPatch={() => {}} />
    </ViewErrorBoundary>,
  );
}

describe("loadViewPlugins", () => {
  it("imports each entry through the API prefix and its kinds render", async () => {
    const importModule = vi.fn(async () => {
      registerViewKind({ kind: "hello", Component: () => <p>hello from a plugin</p> });
    });
    await loadViewPlugins({ list: async () => [info("greeter", ["hello"])], importModule });

    expect(importModule).toHaveBeenCalledWith(`${API_PREFIX}/view-plugins/greeter/index.js`);
    renderKind("hello");
    expect(screen.getByText("hello from a plugin")).toBeInTheDocument();
  });

  it("refuses a plugin built for another SDK major without importing it", async () => {
    const importModule = vi.fn(async () => {});
    await loadViewPlugins({ list: async () => [info("old", ["legacy"], "0")], importModule });

    expect(importModule).not.toHaveBeenCalled();
    renderKind("legacy");
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent('view plugin "old"');
    expect(banner).toHaveTextContent(`SDK 0`);
    expect(banner).toHaveTextContent(`SDK ${SDK_VERSION}`);
  });

  it("an import that throws becomes a per-panel error naming the plugin and the reason", async () => {
    await loadViewPlugins({
      list: async () => [info("broken", ["b1", "b2"])],
      importModule: async () => {
        throw new Error("Failed to resolve module specifier \"react/jsx-runtime\"");
      },
    });
    for (const kind of ["b1", "b2"]) {
      renderKind(kind);
      const banner = screen.getByRole("status");
      expect(banner).toHaveTextContent('view plugin "broken"');
      expect(banner).toHaveTextContent("react/jsx-runtime");
      cleanup();
    }
  });

  it("one broken plugin does not stop the next one loading", async () => {
    await loadViewPlugins({
      list: async () => [info("a-broken", ["ka"]), info("b-fine", ["kb"])],
      importModule: async (url) => {
        if (url.includes("a-broken")) throw new Error("boom");
        registerViewKind({ kind: "kb", Component: () => <p>b is fine</p> });
      },
    });
    renderKind("kb");
    expect(screen.getByText("b is fine")).toBeInTheDocument();
  });

  it("a declared kind the module never registered is a loud error, not 'Unsupported'", async () => {
    await loadViewPlugins({
      list: async () => [info("forgetful", ["declared"])],
      importModule: async () => {},
    });
    renderKind("declared");
    expect(screen.getByRole("status")).toHaveTextContent('view plugin "forgetful"');
    expect(screen.getByRole("status")).toHaveTextContent("did not register");
  });

  it("a kind already taken (e.g. by ext/) keeps its incumbent", async () => {
    registerViewKind({ kind: "taken", Component: () => <p>incumbent</p> });
    touched.add("taken");
    await loadViewPlugins({
      list: async () => [info("usurper", ["taken"])],
      importModule: async () => {
        registerViewKind({ kind: "taken", Component: () => <p>usurper</p> });
      },
    });
    renderKind("taken");
    expect(screen.getByText("incumbent")).toBeInTheDocument();
  });

  it("an import that never settles times out instead of holding the first render", async () => {
    vi.useFakeTimers();
    const done = loadViewPlugins({
      list: async () => [info("stuck", ["slow"])],
      importModule: () => new Promise(() => {}),
      timeoutMs: 1000,
    });
    await vi.advanceTimersByTimeAsync(1001);
    await done;
    vi.useRealTimers();
    renderKind("slow");
    expect(screen.getByRole("status")).toHaveTextContent("timed out");
  });

  it("a list that never answers is given up on, so the app still mounts", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const importModule = vi.fn();
    let settled = false;
    const done = loadViewPlugins({ list: () => new Promise(() => {}), importModule, timeoutMs: 1000 }).then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(1001);
    await done;
    expect(settled).toBe(true);
    expect(importModule).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("plugins load side by side: two hung imports cost one timeout, not two", async () => {
    vi.useFakeTimers();
    let settled = false;
    const done = loadViewPlugins({
      list: async () => [info("h1", ["hang1"]), info("h2", ["hang2"])],
      importModule: () => new Promise(() => {}),
      timeoutMs: 1000,
    }).then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(1001);
    expect(settled).toBe(true);
    await done;
    vi.useRealTimers();
    renderKind("hang2");
    expect(screen.getByRole("status")).toHaveTextContent("timed out");
  });

  it("a list that fails to load leaves the app running with no plugins", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(
      loadViewPlugins({
        list: async () => {
          throw new Error("401");
        },
        importModule: vi.fn(),
      }),
    ).resolves.toBeUndefined();
    spy.mockRestore();
  });

  it("a kind nobody declared still says Unsupported view kind", () => {
    renderKind("never-heard-of-it");
    expect(screen.getByText(/Unsupported view kind: never-heard-of-it/)).toBeInTheDocument();
  });
});
