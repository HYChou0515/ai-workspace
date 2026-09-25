/**
 * The shared-module build (#847/#848 PR1 P1): the pure halves of
 * `vite-plugins/sharedModules.ts`. The built bundle itself is checked by
 * `scripts/check-shared-build.mjs`, which `pnpm run build` runs — a build takes
 * ~40 s, too slow for this suite.
 */
import { describe, expect, it } from "vitest";

import {
  SDK_SPECIFIER,
  SHARED_MODULES,
  facadeSource,
  importMap,
  sharedExportNames,
  sharedInputs,
} from "../vite-plugins/sharedModules";

describe("facadeSource", () => {
  it("re-exports every key BY NAME, because React ships CommonJS", () => {
    const src = facadeSource("react", ["useState", "useEffect"]);
    expect(src).toContain('import __m from "react";');
    expect(src).toContain("export default __m;");
    expect(src).toContain("export const useState = __m.useState;");
    expect(src).toContain("export const useEffect = __m.useEffect;");
  });
});

describe("sharedExportNames", () => {
  it("lists React's real named exports, useState among them", () => {
    expect(sharedExportNames("react")).toContain("useState");
    expect(sharedExportNames("react/jsx-runtime")).toEqual(expect.arrayContaining(["jsx", "jsxs", "Fragment"]));
    expect(sharedExportNames("react-dom/client")).toContain("createRoot");
  });

  it("drops what cannot be a named export", () => {
    for (const spec of Object.keys(SHARED_MODULES)) {
      const names = sharedExportNames(spec);
      expect(names).not.toContain("default");
      expect(names).not.toContain("__esModule");
      for (const n of names) expect(n).toMatch(/^[A-Za-z_$][\w$]*$/);
    }
  });
});

describe("importMap", () => {
  it("points every shared specifier at a fixed shared/ file in a build", () => {
    const map = importMap("build", "/");
    expect(map.imports.react).toBe("/shared/react.js");
    expect(map.imports["react/jsx-runtime"]).toBe("/shared/react-jsx-runtime.js");
    expect(Object.keys(map.imports).sort()).toEqual([...Object.keys(SHARED_MODULES), SDK_SPECIFIER].sort());
  });

  it("honours a sub-path base", () => {
    expect(importMap("build", "/my-svc/rca/").imports.react).toBe("/my-svc/rca/shared/react.js");
  });

  it("points at the facades as Vite serves them under the dev server", () => {
    // Never a `.vite/deps` URL — its `?v=` hash changes on every re-optimise.
    const map = importMap("serve", "/");
    expect(map.imports.react).toBe("/@id/__x00__shared:react");
    for (const url of Object.values(map.imports)) expect(url).not.toContain(".vite/deps");
  });
});

describe("the view SDK", () => {
  it("is the host's own public barrel: shared/view-sdk.js in a build, the source under dev", () => {
    expect(SDK_SPECIFIER).toBe("@aiws/view-sdk");
    expect(importMap("build", "/").imports[SDK_SPECIFIER]).toBe("/shared/view-sdk.js");
    expect(importMap("serve", "/").imports[SDK_SPECIFIER]).toBe("/src/renderers/entity/public.ts");
    expect(sharedInputs()["shared/view-sdk"]).toMatch(/src\/renderers\/entity\/public\.ts$/);
  });
});
