import { describe, expect, it } from "vitest";

import { describeFailure } from "./failure";
import { translate } from "./i18n";

const zh = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

describe("describeFailure (docs/plan-friendly-errors.md D1/D5)", () => {
  it("words a rate limit from its kind, with how long it waited, and keeps the raw text", () => {
    const out = describeFailure(
      { kind: "rate_limited", params: { waited_s: 120 }, raw: "RateLimitError: litellm.RateLimitError: slow down" },
      "zh-TW",
    );

    expect(out.headline).toBe(zh("failure.rate_limited.waited", { waited_s: 120 }));
    expect(out.headline).toContain("120");
    expect(out.action).toBe(zh("failure.rate_limited.do"));
    expect(out.details).toBe("RateLimitError: litellm.RateLimitError: slow down");
  });

  it("has copy for every kind the backend names", () => {
    // `failover.classify.ERROR_KINDS` — kept in step by hand, so a kind the
    // backend adds without copy shows up here as the unknown wording.
    for (const kind of [
      "rate_limited",
      "all_busy",
      "auth",
      "timeout",
      "connection",
      "provider_error",
      "context_overflow",
      "bad_request",
      "unknown",
    ]) {
      const out = describeFailure({ kind, raw: "x" }, "zh-TW");
      expect(out.headline, kind).not.toBe("");
      expect(out.action, kind).not.toBeNull();
      if (kind !== "unknown") expect(out.headline, kind).not.toBe(zh("failure.unknown.what"));
    }
  });

  it("reads a kind it has no copy for — and an old row's `error` — as unknown", () => {
    for (const kind of ["error", "something_new"]) {
      const out = describeFailure({ kind, raw: "Boom: it broke" }, "zh-TW");
      expect(out.headline).toBe(zh("failure.unknown.what"));
      expect(out.details).toBe("Boom: it broke");
    }
  });

  it("says nothing about how long or how often when the backend did not count it", () => {
    const out = describeFailure({ kind: "timeout", raw: "TimeoutError: slow" }, "zh-TW");
    expect(out.headline).toBe(zh("failure.timeout.what"));
    expect(out.headline).not.toContain("{");
  });

  it("lets a writer that already has the whole sentence keep it", () => {
    const out = describeFailure(
      { kind: "quota", headline: "工作區已滿", raw: "507 Insufficient Storage" },
      "zh-TW",
    );
    expect(out.headline).toBe("工作區已滿");
    expect(out.details).toBe("507 Insufficient Storage");
  });

  it("has no details to show when the raw text says nothing the headline did not", () => {
    expect(describeFailure({ kind: "unknown", raw: "" }, "zh-TW").details).toBeNull();
    expect(
      describeFailure({ kind: "quota", headline: "工作區已滿", raw: "工作區已滿" }, "zh-TW").details,
    ).toBeNull();
  });
});
