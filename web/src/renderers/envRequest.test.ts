/** The card `request_env` declared (docs/plan-env-request-card.md). */
import { describe, expect, it } from "vitest";

import { parseEnvRequest } from "./envRequest";

const MARKER = "\n[env-request]";

describe("parseEnvRequest", () => {
  it("reads the declared tool, names and reason", () => {
    const out = `The user now sees a card.${MARKER}{"tool":"lookup","names":["ERP_TOKEN"],"reason":"ERP needs a sign-in"}`;

    expect(parseEnvRequest(out)).toEqual({
      tool: "lookup",
      names: ["ERP_TOKEN"],
      reason: "ERP needs a sign-in",
    });
  });

  it("is nothing for a refusal, prose, or a reply still streaming", () => {
    expect(parseEnvRequest("error: `exec` does not receive …")).toBeNull();
    expect(parseEnvRequest(`x${MARKER}{"tool":"lookup","names":[`)).toBeNull();
    expect(parseEnvRequest(undefined)).toBeNull();
  });

  it("is nothing when the declaration is not the shape the tool writes", () => {
    expect(parseEnvRequest(`x${MARKER}{"tool":"lookup","names":[],"reason":"r"}`)).toBeNull();
    expect(parseEnvRequest(`x${MARKER}{"tool":"lookup","names":[1],"reason":"r"}`)).toBeNull();
    expect(parseEnvRequest(`x${MARKER}["lookup"]`)).toBeNull();
  });
});
