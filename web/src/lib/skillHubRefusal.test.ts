import { describe, expect, it } from "vitest";

import { HttpError } from "../api/http";
import { translate } from "./i18n";
import { describeRefusal } from "./skillHubRefusal";

const t = (
  key: Parameters<typeof translate>[1],
  vars?: Record<string, string | number>,
) => translate("zh-TW", key, vars);

const coded = (status: number, detail: Record<string, unknown>) =>
  new HttpError(
    status,
    `fallback (${status})`,
    String(detail.error),
    undefined,
    detail,
  );

describe("describeRefusal (plan-skill-hub-ui-polish D16)", () => {
  it.each([
    [coded(404, { error: "not_found" }), t("skillHub.refused.not_found")],
    [coded(403, { error: "owner_only" }), t("skillHub.refused.owner_only")],
    [
      coded(400, { error: "transfer_owner_required" }),
      t("skillHub.refused.transfer_owner_required"),
    ],
    [
      coded(409, {
        error: "transfer_name_taken",
        owner: "bob",
        name: "csv-peek",
      }),
      t("skillHub.refused.transfer_name_taken", {
        owner: "bob",
        name: "csv-peek",
      }),
    ],
    [
      coded(409, {
        error: "folder_in_the_way",
        owner: "alice",
        path: ".skill/csv-peek/",
      }),
      t("skillHub.refused.folder_in_the_way.theirs", {
        owner: "alice",
        path: ".skill/csv-peek/",
      }),
    ],
    [
      coded(409, {
        error: "folder_in_the_way",
        owner: "",
        path: ".skill/csv-peek/",
      }),
      t("skillHub.refused.folder_in_the_way", { path: ".skill/csv-peek/" }),
    ],
  ])("words each code in the viewer's language: %s", (err, expected) => {
    expect(describeRefusal(err, t)).toBe(expected);
  });

  it("falls back to the message for an unknown code, a plain sentence, or a non-HTTP error", () => {
    expect(describeRefusal(coded(418, { error: "teapot" }), t)).toBe(
      "fallback (418)",
    );
    expect(
      describeRefusal(new HttpError(409, "the server's own sentence"), t),
    ).toBe("the server's own sentence");
    expect(describeRefusal(new Error("boom"), t)).toBe("boom");
    expect(describeRefusal("plain", t)).toBe("plain");
  });
});
