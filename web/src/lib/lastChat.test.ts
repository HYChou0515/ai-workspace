// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";

import { recallLastChat, rememberLastChat } from "./lastChat";

afterEach(() => localStorage.clear());

describe("per-App last chat", () => {
  it("remembers and recalls the last chat separately for each App", () => {
    rememberLastChat("playground", "playground-item:1");
    rememberLastChat("topic-hub", "topic-hub-item:9");
    expect(recallLastChat("playground")).toBe("playground-item:1");
    expect(recallLastChat("topic-hub")).toBe("topic-hub-item:9");
  });

  it("returns null for an App with no remembered chat", () => {
    expect(recallLastChat("playground")).toBeNull();
  });
});
