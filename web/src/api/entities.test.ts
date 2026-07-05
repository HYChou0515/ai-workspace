import { describe, expect, it, vi } from "vitest";

vi.mock("./http", () => ({ apiFetch: vi.fn() }));
import { entitiesApi, EntityConflictError } from "./entities";
import { apiFetch } from "./http";

const record = { number: 1, type_name: "issue", fields: {}, body: "", diagnostics: [] };
const okJson = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as unknown as Response;
const errStatus = (status: number) =>
  ({ ok: false, status, json: async () => ({ detail: "nope" }) }) as unknown as Response;

describe("entitiesApi.update", () => {
  it("sends the expected_version in the PUT body when provided (§C6 optimistic lock)", async () => {
    vi.mocked(apiFetch).mockResolvedValue(okJson(record));
    await entitiesApi.update("rca", "item-1", "issue", 1, { status: "done" }, "v1");
    expect(apiFetch).toHaveBeenCalledWith(
      "/a/rca/items/item-1/entities/issue/1",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ patch: { status: "done" }, expected_version: "v1" }),
      }),
    );
  });

  it("omits expected_version when not provided (last-write-wins)", async () => {
    vi.mocked(apiFetch).mockResolvedValue(okJson(record));
    await entitiesApi.update("rca", "item-1", "issue", 1, { status: "done" });
    const body = JSON.parse(vi.mocked(apiFetch).mock.calls.at(-1)![1]!.body as string);
    expect(body).toEqual({ patch: { status: "done" } });
  });

  it("throws EntityConflictError on a 409 so the UI can reload instead of clobbering", async () => {
    vi.mocked(apiFetch).mockResolvedValue(errStatus(409));
    await expect(
      entitiesApi.update("rca", "item-1", "issue", 1, { status: "done" }, "stale"),
    ).rejects.toBeInstanceOf(EntityConflictError);
  });

  it("throws a plain Error (not a conflict) on other failures", async () => {
    vi.mocked(apiFetch).mockResolvedValue(errStatus(500));
    const err = await entitiesApi
      .update("rca", "item-1", "issue", 1, { status: "done" })
      .catch((e) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(EntityConflictError);
  });
});
