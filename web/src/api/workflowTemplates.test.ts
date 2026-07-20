import { afterEach, describe, expect, it, vi } from "vitest";

import { workflowTemplatesApi, TemplateConflictError } from "./workflowTemplates";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("workflowTemplatesApi", () => {
  it("lists templates", async () => {
    mockFetch(200, [
      { id: "image-to-knowledge", title: "T", description: "d", phases: [], compatible: true, problems: [] },
    ]);
    const out = await workflowTemplatesApi.list("playground", "i1");
    expect(out[0].id).toBe("image-to-knowledge");
    expect(out[0].compatible).toBe(true);
  });

  it("copies a template into the item", async () => {
    const fn = mockFetch(200, { workflow_id: "image-to-knowledge", path: "/.workflows/x.json" });
    await workflowTemplatesApi.copy("playground", "i1", "image-to-knowledge");
    expect(String(fn.mock.calls[0][0])).toContain(
      "/workflow-templates/image-to-knowledge/copy",
    );
    // no overwrite unless asked — the default must never clobber the user's edits
    expect(String(fn.mock.calls[0][0])).not.toContain("overwrite=true");
  });

  it("asks for overwrite explicitly", async () => {
    const fn = mockFetch(200, { workflow_id: "x", path: "/p" });
    await workflowTemplatesApi.copy("playground", "i1", "x", { overwrite: true });
    expect(String(fn.mock.calls[0][0])).toContain("overwrite=true");
  });

  it("raises a distinguishable error on a name clash so the UI can offer to replace", async () => {
    mockFetch(409, { detail: "this item already has a workflow named 'x'" });
    await expect(workflowTemplatesApi.copy("playground", "i1", "x")).rejects.toBeInstanceOf(
      TemplateConflictError,
    );
  });

  it("surfaces the server's reason when the profile cannot run the template", async () => {
    mockFetch(422, { detail: "tool 'read_image' is outside the profile's allowed tools" });
    await expect(workflowTemplatesApi.copy("playground", "i1", "x")).rejects.toThrow(
      /read_image/,
    );
  });
});
