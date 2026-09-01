/**
 * `.env`-shaped text, in and out.
 *
 * We parse it ourselves rather than handing it to anything shell-flavoured: a
 * value carrying `$`, a backtick or `$(…)` must survive a round trip through
 * this file exactly as typed, because the only symptom of a mangled key is a
 * tool failing somewhere else entirely.
 */
import { describe, expect, it } from "vitest";

import { mergeEnv, parseEnvText, setEnvValue, toEnvText } from "./envFile";

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

describe("setEnvValue", () => {
  it("keeps the comments and blank lines around what it changed", () => {
    // The reason this exists rather than a round trip through the map (#750).
    // A form field writes through here on every keystroke, and a round trip
    // keeps only what the map can hold — so typing into one field would
    // silently delete the notes someone wrote above it.
    const typed = "# prod keys\nAPI_KEY=old\n\n# ask ops before changing\nREGION=tw\n";
    expect(setEnvValue(typed, "API_KEY", "new")).toBe(
      "# prod keys\nAPI_KEY=new\n\n# ask ops before changing\nREGION=tw\n",
    );
  });

  it("keeps a line that is still being typed", () => {
    // A name with no `=` yet is what half-typed looks like. It is not
    // something the map can carry, so a round trip drops it — mid-sentence,
    // while the person is still looking at it.
    expect(setEnvValue("API_KEY=1\nHALF_TYPED", "REGION", "tw")).toBe(
      "API_KEY=1\nHALF_TYPED\nREGION=tw\n",
    );
  });

  it("appends a name that is not there yet", () => {
    expect(setEnvValue("A=1\n", "B", "2")).toBe("A=1\nB=2\n");
  });

  it("starts a file that was empty", () => {
    expect(setEnvValue("", "A", "1")).toBe("A=1\n");
  });

  it("rewrites the LAST assignment, which is the one that counts", () => {
    // dotenv keeps the last of a repeated name, and so does `parseEnvText`.
    // Rewriting the first would leave the panel showing one value while the
    // stored set took the other.
    expect(setEnvValue("A=1\nA=2\n", "A", "3")).toBe("A=1\nA=3\n");
    expect(parseEnvText(setEnvValue("A=1\nA=2\n", "A", "3")).A).toBe("3");
  });

  it("does not mistake a commented-out name for the real one", () => {
    expect(setEnvValue("#A=old\nA=live\n", "A", "new")).toBe("#A=old\nA=new\n");
  });

  it("matches a name written with `export`, the way the parser does", () => {
    expect(setEnvValue("export A=1\n", "A", "2")).toBe("A=2\n");
  });
});
