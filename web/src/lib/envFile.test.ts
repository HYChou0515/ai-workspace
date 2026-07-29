/**
 * `.env`-shaped text, in and out.
 *
 * We parse it ourselves rather than handing it to anything shell-flavoured: a
 * value carrying `$`, a backtick or `$(…)` must survive a round trip through
 * this file exactly as typed, because the only symptom of a mangled key is a
 * tool failing somewhere else entirely.
 */
import { describe, expect, it } from "vitest";

import { mergeEnv, parseEnvText, toEnvText } from "./envFile";

describe("parseEnvText", () => {
  it("reads one variable per line", () => {
    expect(parseEnvText("API_KEY=sk-1\nREGION=tw")).toEqual({ API_KEY: "sk-1", REGION: "tw" });
  });

  it("splits on the FIRST = only", () => {
    // Base64 padding and connection strings both put `=` inside the value.
    expect(parseEnvText("TOKEN=a=b=c")).toEqual({ TOKEN: "a=b=c" });
  });

  it("keeps a value exactly as written", () => {
    const tricky = "a b#c$d`e'f\"g$(echo no)";
    expect(parseEnvText(`TOKEN=${tricky}`)).toEqual({ TOKEN: tricky });
  });

  it("ignores blank lines and comments", () => {
    expect(parseEnvText("\n# a note\n  \nA=1\n")).toEqual({ A: "1" });
  });

  it("tolerates CRLF", () => {
    // A `.env` written on Windows, or pasted out of one.
    expect(parseEnvText("A=1\r\nB=2\r\n")).toEqual({ A: "1", B: "2" });
  });

  it("trims the whitespace around both halves", () => {
    // Edge whitespace in a `.env` is invisible and almost always accidental —
    // a key with a stray trailing space fails in a way nobody can see. A value
    // that genuinely needs one is simply not expressible in this interchange
    // format; the panel itself still stores whatever was typed.
    expect(parseEnvText("  A = 1 ")).toEqual({ A: "1" });
  });

  it("keeps whitespace INSIDE a value", () => {
    expect(parseEnvText("A=one two  three")).toEqual({ A: "one two  three" });
  });

  it("skips a line with no assignment rather than inventing one", () => {
    expect(parseEnvText("just some prose\nA=1")).toEqual({ A: "1" });
  });

  it("skips a nameless assignment", () => {
    expect(parseEnvText("=orphan\nA=1")).toEqual({ A: "1" });
  });

  it("drops a `export ` prefix, which is what real .env files carry", () => {
    expect(parseEnvText("export A=1")).toEqual({ A: "1" });
  });
});

describe("toEnvText", () => {
  it("writes one variable per line", () => {
    expect(toEnvText({ A: "1", B: "2" })).toBe("A=1\nB=2\n");
  });

  it("round-trips a value with the characters real keys carry", () => {
    const vars = { TOKEN: "a=b c#d$e`f'g\"h" };
    expect(parseEnvText(toEnvText(vars))).toEqual(vars);
  });

  it("is empty for nothing at all", () => {
    expect(toEnvText({})).toBe("");
  });
});

describe("mergeEnv", () => {
  it("overwrites a name the import mentions", () => {
    expect(mergeEnv({ A: "old" }, { A: "new" })).toEqual({ A: "new" });
  });

  it("leaves alone a name the import does NOT mention", () => {
    // The decision: import MERGES. Replace-all would silently delete variables
    // the imported file happens not to carry, and delete has its own button.
    expect(mergeEnv({ A: "1", B: "2" }, { A: "9" })).toEqual({ A: "9", B: "2" });
  });

  it("adds names that were not there", () => {
    expect(mergeEnv({ A: "1" }, { B: "2" })).toEqual({ A: "1", B: "2" });
  });

  it("importing nothing changes nothing", () => {
    expect(mergeEnv({ A: "1" }, {})).toEqual({ A: "1" });
  });
});
