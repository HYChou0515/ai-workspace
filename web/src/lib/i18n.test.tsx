// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  detectLocale,
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

  it("de-jargons the sandbox idle banner to 'execution environment' / 執行環境", () => {
    expect(translate("en", "banner.sandboxIdle")).toContain("execution environment");
    expect(translate("en", "banner.sandboxIdle")).not.toContain("workspace");
    expect(translate("zh-TW", "banner.sandboxIdle")).toContain("執行環境");
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
