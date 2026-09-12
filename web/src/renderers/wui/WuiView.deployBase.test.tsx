// @vitest-environment happy-dom
/**
 * The Deploy address on a SUB-PATH deploy. Vite bakes the deploy base into
 * `import.meta.env.BASE_URL` and the router mounts under it (`App.tsx`), so a
 * link that starts at the origin points outside the SPA — the proxy's 404, or
 * another service. `API_BASE` is the helper written for exactly this ("for
 * linking to a client-side SPA route"). Its own file, because the mock is
 * module-wide and every other Deploy test asserts the base-less form.
 */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { QueryWrap } from "../../test/queryWrapper";
import type { ViewSpec } from "../entity/types";
import { WuiView } from "./WuiView";

vi.mock("../../api/http", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  API_BASE: "/my-svc/rca",
}));

afterEach(() => vi.unstubAllGlobals());

describe("WuiView: Deploy under a sub-path deploy", () => {
  it("puts the deploy base in front of the route, or the link leaves the SPA", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const body = "<html><body>v1</body></html>";
    const fs = {
      scopeId: "item1",
      caps: { write: true, delete: true },
      readFile: vi.fn(async (path: string) => {
        if (path !== "/sales/index.html") throw new Error(`not found: ${path}`);
        return { kind: "text", path, size: body.length, text: body, encoding: "utf-8" };
      }),
      fileDownloadUrl: (path: string) => `/api/files${path}`,
    } as unknown as FileService;
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={fs}>
            <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
    const deploy = await screen.findByRole("button", { name: /^deploy$/i });
    fireEvent.click(deploy);

    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(
      `${window.location.origin}/my-svc/rca/w/rca/item1/sales/page.ai.yaml`,
    );
  });
});
