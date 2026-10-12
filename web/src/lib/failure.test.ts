import { describe, expect, it } from "vitest";

import { describeFailure, docFailure, failureFromHttp } from "./failure";
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

describe("failureFromHttp — our own API refusing a request (docs/plan-friendly-errors.md P6)", () => {
  it("names a permission refusal and a server fault, and keeps the status line as details", () => {
    const forbidden = describeFailure(
      failureFromHttp(Object.assign(new Error("messages failed: 403"), { status: 403 })),
      "zh-TW",
    );
    expect(forbidden.headline).toBe(zh("failure.forbidden.what"));
    expect(forbidden.details).toBe("messages failed: 403");

    const fault = describeFailure(
      failureFromHttp(Object.assign(new Error("messages failed: 500"), { status: 500 })),
      "zh-TW",
    );
    expect(fault.headline).toBe(zh("failure.server_error.what"));
    expect(fault.action).toBe(zh("failure.server_error.do"));
  });

  it("does not guess at anything else — the raw text stays, under unknown", () => {
    const out = describeFailure(
      failureFromHttp(Object.assign(new Error("messages failed: 409"), { status: 409 })),
      "zh-TW",
    );
    expect(out.headline).toBe(zh("failure.unknown.what"));
    expect(out.details).toBe("messages failed: 409");
  });
});

describe("docFailure — a document that failed to process (docs/plan-friendly-errors.md P5)", () => {
  it("words it from the kind the backend named, keeping the detail as the raw text", () => {
    const f = docFailure(
      { status_detail: "RateLimitError: slow down", status_error_kind: "rate_limited" },
      "zh-TW",
    );
    expect(describeFailure(f, "zh-TW").headline).toBe(zh("failure.rate_limited.what"));
    expect(describeFailure(f, "zh-TW").details).toBe("RateLimitError: slow down");
  });

  it("falls back to the page's own sentence when nothing named it — an older row, an interruption", () => {
    const f = docFailure({ status_detail: "processing was interrupted", status_error_kind: null }, "zh-TW");
    expect(describeFailure(f, "zh-TW").headline).toBe("處理失敗");
    expect(describeFailure(f, "zh-TW").details).toBe("processing was interrupted");
  });
});

describe("a document failure's remedy is a document's, not a chat's (P5)", () => {
  it("offers re-reading the document rather than sending a message again", () => {
    const f = docFailure(
      { status_detail: "RateLimitError: slow down", status_error_kind: "rate_limited" },
      "zh-TW",
    );
    expect(describeFailure(f, "zh-TW").action).toBe(zh("kb.doc.failedAction"));
    expect(describeFailure(f, "zh-TW").action).not.toBe(zh("failure.rate_limited.do"));
  });
});
