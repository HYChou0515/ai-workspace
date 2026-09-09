// @vitest-environment happy-dom
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  detectLocale,
  messages,
  getStoredLocale,
  initialLocale,
  LocaleProvider,
  setStoredLocale,
  translate,
  useLocale,
  useT,
} from "./i18n";

function Probe() {
  const t = useT();
  const [locale, setLocale] = useLocale();
  return (
    <div>
      <span data-testid="label">{t("settings.language")}</span>
      <span data-testid="locale">{locale}</span>
      <button onClick={() => setLocale("en")}>to-en</button>
    </div>
  );
}

/**
 * Keys whose "environment" is NOT the sandbox. A named list rather than a `env.`
 * prefix skip, for two reasons: a future `env.sandboxStatus` would have been
 * silently exempt from both halves of the sweep, and a genuine
 * deployment-environment string should have to be entered here deliberately
 * rather than acquire an exemption by being named a certain way.
 */
const NOT_THE_SANDBOX = new Set([
  "env.button", // 環境變數 / Env — the variables handed to tools
  "env.title",
]);

// NOT `import.meta.url`: this file runs under happy-dom, where that is a
// browser-style URL and its pathname is `/src/lib/` rather than a real path.
// Vitest's cwd is `web/`.
const SRC = join(process.cwd(), "src");

/**
 * What a hardcoded string may not say. Assembled from escapes rather than
 * written out, so the lines doing the banning are not themselves offences —
 * exempting this whole file instead would blind the guard to the place most
 * likely to grow a stale copy.
 *
 * The zh needle is 環境 alone, NOT 執行環境. The first version banned the
 * four-character form and would therefore have caught nothing: both strings
 * this rename actually had to hand-fix (`AgentPanel`'s compaction hints) said
 * 「這個環境…」. A guard aimed past its own stated reason for existing is worse
 * than none, because it reports success.
 *
 * 環境變數 — environment VARIABLES — is a different noun and is removed before
 * the check rather than allow-listed by key, because in source there is no key
 * to list.
 *
 * English cannot ban the bare word: `itemEnvironmentApi`, the `/environment`
 * route and this component's own name are all legitimate. It bans the PROSE the
 * rename removed.
 */
const ZH_TERM = "\u74b0\u5883";

/**
 * Compounds that are a different noun, removed from a line before it is judged.
 * In source there is no key to allow-list against, so the door has to be the
 * words themselves — and it has to exist: this branch put 部署環境 into the docs
 * for a sentence that really is about the host, and 開發環境 / 正式環境 are the
 * same kind of word. Without them the guard would forbid writing about a
 * deployment at all, under a failure message about mis-naming the sandbox.
 */
const ZH_ALLOWED = [
  "\u74b0\u5883\u8b8a\u6578", // 環境變數 — environment variables
  "\u90e8\u7f72\u74b0\u5883", // 部署環境 — the deployment
  "\u958b\u767c\u74b0\u5883", // 開發環境
  "\u6b63\u5f0f\u74b0\u5883", // 正式環境
  "\u6e2c\u8a66\u74b0\u5883", // 測試環境
];
// Split so this line is not itself an offence, same reason as the zh needles.
const EN_TERMS = [
  new RegExp("execution " + "environment", "i"),
  new RegExp("live " + "environments?", "i"),
];

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

describe("i18n translate", () => {
  it("returns the string for the active locale", () => {
    expect(translate("zh-TW", "settings.language")).toBe("語言");
    expect(translate("en", "settings.language")).toBe("Language");
  });

  it("interpolates {named} placeholders", () => {
    expect(translate("en", "banner.maxTurns", { turns: 12 })).toBe(
      "Reached the turn limit (12); the conversation stopped.",
    );
    expect(translate("zh-TW", "banner.maxTurns", { turns: 12 })).toContain("12");
    expect(translate("zh-TW", "banner.maxTurns", { turns: 12 })).not.toContain("{turns}");
  });
});

describe("i18n #171 term sweep", () => {
  it("unifies the reasoning knob on 'thinking' (en); zh stays 思考", () => {
    expect(translate("en", "picker.effort")).toBe("Thinking depth");
    expect(translate("en", "picker.aria")).toBe("Model and thinking depth");
    expect(translate("zh-TW", "picker.effort")).toBe("思考深度");
  });

  it("renames the knowledge-search knob to 'Search scope' so it no longer collides with thinking depth", () => {
    expect(translate("en", "picker.depth")).toBe("Search scope");
    expect(translate("zh-TW", "picker.depth")).toBe("搜尋範圍");
  });

  it("calls the thing a sandbox — 沙盒 / Sandbox", () => {
    expect(translate("en", "itemenv.button")).toBe("Sandbox");
    expect(translate("zh-TW", "itemenv.button")).toBe("沙盒");
    expect(translate("en", "banner.sandboxIdle")).toContain("sandbox");
    expect(translate("en", "banner.sandboxIdle")).not.toContain("workspace");
    expect(translate("zh-TW", "banner.sandboxIdle")).toContain("沙盒");
  });

  /**
   * #171 went the other way — sandbox → 執行環境 / execution environment — to
   * de-jargon the word. This reverses that decision, and the reason this guard
   * is a sweep rather than one assertion is that the HALF-done rename is worse
   * than either name: the button would read 沙盒 and the panel it opens 執行環境,
   * and nobody could tell whether those were one thing or two.
   *
   * Environment VARIABLES are a different noun and keep theirs — they are the
   * variables handed to tools, and "沙盒變數" would name nothing anyone types.
   */
  it("leaves no string still calling the sandbox an environment", () => {
    for (const [key, entry] of Object.entries(messages)) {
      if (NOT_THE_SANDBOX.has(key)) continue;
      const zh = (entry as Record<string, string>)["zh-TW"];
      const en = (entry as Record<string, string>).en;
      // The key is the MESSAGE, not part of the subject: folding it in meant a
      // future `env.environmentName` failed a guard about what users read, over
      // a word no user reads.
      expect(zh, key).not.toMatch(new RegExp(ZH_TERM));
      expect(en, key).not.toMatch(/environments?\b/i);
    }
  });

  /**
   * `messages` is not the only place a user-visible string lives, and the two
   * this rename actually had to hand-fix were not in it: `AgentPanel` writes
   * some of its own copy as JSX literals. A guard over the table alone would
   * have let the old word back in through them — while `contribution.md` cites
   * this file as the thing that stops exactly that.
   *
   * It reads LINES and skips comment ones, rather than trying to match quoted
   * literals. The obvious `/(["\'`])(...)\1/` spelling is worse than useless
   * here: an apostrophe in English prose opens a "string" that runs to the next
   * quote several lines away, swallowing the comments this rule deliberately
   * spares — it reported three offences on a clean tree, none of them real.
   *
   * The word stays in COMMENTS on purpose: that is where the decision this
   * reverses is recorded, and erasing it leaves the next person re-deciding it
   * from nothing. A trailing comment on a line of code would be a false
   * positive, which is the safe direction for a ban.
   */
  it("leaves no hardcoded literal calling it one either", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          const code = line.trim();
          if (code.startsWith("//") || code.startsWith("*") || code.startsWith("/*")) return;
          const zh = ZH_ALLOWED.reduce((line, ok) => line.split(ok).join(""), code);
          if (zh.includes(ZH_TERM) || EN_TERMS.some((re) => re.test(code))) {
            offenders.push(`${relative(SRC, file)}:${i + 1}`);
          }
        });
    }
    expect(offenders).toEqual([]);
  });

  it("reframes the advanced-retrieval tooltips as outcomes, not mechanisms", () => {
    expect(translate("zh-TW", "depth.expand.title")).toContain("相關文件");
    expect(translate("en", "depth.expand.title")).toMatch(/rephras/i);
    expect(translate("zh-TW", "depth.hyde.title")).toContain("貼近");
    expect(translate("en", "depth.rerank.title")).toMatch(/most relevant/i);
    expect(translate("zh-TW", "depth.rerank.title")).toContain("排到前面");
  });
});

describe("i18n detectLocale", () => {
  it("maps any zh* tag to zh-TW", () => {
    expect(detectLocale("zh-TW")).toBe("zh-TW");
    expect(detectLocale("zh-CN")).toBe("zh-TW");
    expect(detectLocale("ZH")).toBe("zh-TW");
  });

  it("maps any other recognised tag to en", () => {
    expect(detectLocale("en-US")).toBe("en");
    expect(detectLocale("fr")).toBe("en");
  });

  it("falls back to zh-TW when the language is unknown", () => {
    expect(detectLocale(undefined)).toBe("zh-TW");
    expect(detectLocale("")).toBe("zh-TW");
  });
});

describe("i18n locale persistence", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("round-trips a stored locale", () => {
    setStoredLocale("en");
    expect(getStoredLocale()).toBe("en");
  });

  it("returns null when nothing is stored", () => {
    expect(getStoredLocale()).toBeNull();
  });

  it("ignores a corrupt stored value", () => {
    localStorage.setItem("ws.locale", "klingon");
    expect(getStoredLocale()).toBeNull();
  });

  it("initialLocale prefers the stored choice over detection", () => {
    setStoredLocale("en");
    expect(initialLocale()).toBe("en");
  });

  it("initialLocale falls back to navigator detection when unset", () => {
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
    expect(initialLocale()).toBe("zh-TW");
  });
});

describe("LocaleProvider + hooks", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("renders the active locale's copy and switches on setLocale", () => {
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
    render(
      <LocaleProvider>
        <Probe />
      </LocaleProvider>,
    );
    expect(screen.getByTestId("label").textContent).toBe("語言");

    fireEvent.click(screen.getByText("to-en"));
    expect(screen.getByTestId("label").textContent).toBe("Language");
    expect(screen.getByTestId("locale").textContent).toBe("en");
    expect(getStoredLocale()).toBe("en"); // the switch is sticky
  });

  it("works outside a provider, defaulting to zh-TW (untouched components stay safe)", () => {
    render(<Probe />);
    expect(screen.getByTestId("label").textContent).toBe("語言");
    // setLocale is a no-op without a provider — clicking must not throw.
    fireEvent.click(screen.getByText("to-en"));
    expect(screen.getByTestId("label").textContent).toBe("語言");
  });
});
