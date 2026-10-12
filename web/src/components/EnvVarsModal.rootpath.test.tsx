// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// A sub-path deploy (built with VITE_BASE_PATH=/sub): the link from the
// cross-workspace tab to the My environment variables page must carry the
// deploy prefix. A bare `/my-env` left the app for `https://host/my-env`.
vi.mock("../api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/http")>()),
  API_BASE: "/sub",
  API_PREFIX: "/sub/api",
}));

import { renderWithQuery } from "../test/queryWrapper";
import { EnvVarsModal } from "./EnvVarsModal";

afterEach(cleanup);

describe("EnvVarsModal root-path", () => {
  it("links to the My environment variables page under the deploy prefix", async () => {
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        envPolicy={{}}
        onSave={vi.fn()}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => ({ tools: [], updateNeedsClose: false, canClose: false })),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(async () => ({})),
        }}
        privateClient={{
          get: vi.fn(async () => ({ values: {}, auto: {} })),
          put: vi.fn(async () => {}),
          clear: vi.fn(async () => {}),
        }}
        personalClient={{
          get: vi.fn(async () => ({ values: {}, updated: {} })),
          put: vi.fn(async (values: Record<string, string>) => ({ values, updated: {} })),
        }}
      />,
    );
    fireEvent.click(await screen.findByTestId("env-tab-personal"));
    expect(await screen.findByTestId("env-personal-page")).toHaveAttribute("href", "/sub/my-env");
  });
});
