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
