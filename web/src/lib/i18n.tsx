/**
 * Minimal hand-rolled i18n (#160). A central typed message catalog keyed by a
 * dotted string; `useT()` returns a `t(key)` bound to the active locale. No
 * dependency, no plural/interpolation machinery — every #160 string is static,
 * so the surface stays tiny. Adopted incrementally: a component routed through
 * `t()` adds its keys here; untouched components keep their inline English.
 */

import { createContext, useCallback, useContext, useState } from "react";

export type Locale = "zh-TW" | "en";

/** Each entry carries both locales, so a missing translation is a type error. */
type Entry = Record<Locale, string>;

export const messages = {
  // Settings panel (WorkspaceShell)
  "settings.title": { "zh-TW": "設定", en: "Settings" },
  "settings.theme": { "zh-TW": "外觀", en: "Appearance" },
  "settings.theme.note": {
    "zh-TW": "「系統」會跟隨你的作業系統外觀。",
    en: "“System” follows your OS appearance.",
  },
  "theme.system": { "zh-TW": "系統", en: "System" },
  "theme.light": { "zh-TW": "淺色", en: "Light" },
  "theme.dark": { "zh-TW": "深色", en: "Dark" },
  "settings.language": { "zh-TW": "語言", en: "Language" },
  "settings.about": { "zh-TW": "關於", en: "About" },
  "about.product": { "zh-TW": "產品", en: "Product" },
  "about.signin": { "zh-TW": "登入方式", en: "Sign-in" },
  "about.signin.value": { "zh-TW": "單人示範（免登入）", en: "Single-user demo (no sign-in)" },
  "about.docs": { "zh-TW": "開發者文件", en: "Developer docs" },
  "about.docs.link": { "zh-TW": "API 文件", en: "API reference" },

  // Model + reasoning-depth picker (ModelEffortPicker)
  "picker.aria": { "zh-TW": "模型與思考深度", en: "Model and reasoning depth" },
  "picker.model": { "zh-TW": "模型", en: "Model" },
  "picker.default": { "zh-TW": "預設", en: "Default" },
  "picker.effort": { "zh-TW": "思考深度", en: "Reasoning depth" },
  "effort.low": { "zh-TW": "快速", en: "Quick" },
  "effort.medium": { "zh-TW": "一般", en: "Standard" },
  "effort.high": { "zh-TW": "深入", en: "Deep" },
  "effort.low.note": { "zh-TW": "回答最快，思考較淺", en: "Fastest answer, lighter thinking" },
  "effort.medium.note": { "zh-TW": "深度均衡", en: "Balanced depth" },
  "effort.high.note": { "zh-TW": "較慢但更完整", en: "Slower but more thorough" },
  "picker.footer.low": { "zh-TW": "最快、最輕", en: "Fastest, lightest" },
  "picker.footer.medium": { "zh-TW": "速度適中", en: "Balanced speed" },
  "picker.footer.high": { "zh-TW": "較慢但更完整", en: "Slower, more thorough" },
  "picker.done": { "zh-TW": "完成", en: "Done" },

  // Knowledge-search depth (KB surface)
  "picker.depth": { "zh-TW": "知識搜尋深度", en: "Knowledge search depth" },
  "picker.advanced": { "zh-TW": "進階", en: "Advanced" },
  "depth.quick": { "zh-TW": "快速", en: "Quick" },
  "depth.standard": { "zh-TW": "標準", en: "Standard" },
  "depth.thorough": { "zh-TW": "徹底", en: "Thorough" },
  "depth.quick.note": {
    "zh-TW": "最快——直接用你的字詞搜尋",
    en: "Fastest — searches your words as-is",
  },
  "depth.standard.note": {
    "zh-TW": "輕度擴充查詢（建議）",
    en: "Light query expansion (recommended)",
  },
  "depth.thorough.note": {
    "zh-TW": "搜尋最廣——最慢、命中率最高",
    en: "Widest search — slowest, highest recall",
  },
  "depth.custom.note": {
    "zh-TW": "已自訂——選上方任一級別會覆蓋它。",
    en: "Customised — picking a level above replaces it.",
  },
  "depth.expand": { "zh-TW": "換句話多問幾種", en: "Alternative phrasings" },
  "depth.expand.title": {
    "zh-TW": "額外產生幾種替代問法（0＝關閉）",
    en: "Alternative query phrasings to generate (0 = off)",
  },
  "depth.hyde": { "zh-TW": "先擬假設答案再搜", en: "Hypothetical-answer probes" },
  "depth.hyde.title": {
    "zh-TW": "先擬幾份假設文件再嵌入比對（0＝關閉）",
    en: "Hypothetical-document probes to embed (0 = off)",
  },
  "depth.rerank": { "zh-TW": "讓 AI 重新排序結果", en: "Let AI re-rank results" },
  "depth.rerank.title": {
    "zh-TW": "用 AI 對合併後的候選重新排序",
    en: "LLM-rerank the merged candidate set",
  },
  "picker.wiki": { "zh-TW": "一併查知識百科", en: "Also search the wiki" },
  "picker.wiki.title": {
    "zh-TW": "同時參考 AI 維護的知識百科",
    en: "Also consult the AI-maintained wiki for this question",
  },
} satisfies Record<string, Entry>;

export type MsgKey = keyof typeof messages;

export function translate(locale: Locale, key: MsgKey): string {
  return messages[key][locale];
}

/** Pick a locale from a BCP-47 tag (e.g. `navigator.language`): any `zh*`
 * stays Traditional Chinese, any other recognised tag is English, and an
 * absent/blank tag falls back to zh-TW (the primary audience). */
export function detectLocale(lang: string | undefined): Locale {
  if (!lang) return "zh-TW";
  return lang.toLowerCase().startsWith("zh") ? "zh-TW" : "en";
}

const STORE_KEY = "ws.locale";
const LOCALES: Locale[] = ["zh-TW", "en"];

/** The user's sticky locale override, or null if they've never picked one. */
export function getStoredLocale(): Locale | null {
  try {
    const v = localStorage.getItem(STORE_KEY);
    return v && (LOCALES as string[]).includes(v) ? (v as Locale) : null;
  } catch {
    return null;
  }
}

export function setStoredLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORE_KEY, locale);
  } catch {
    /* localStorage unavailable (private mode / SSR) — choice just isn't sticky */
  }
}

/** The locale to start with: the stored override wins; otherwise detect from
 * the browser. */
export function initialLocale(): Locale {
  return (
    getStoredLocale() ??
    detectLocale(typeof navigator === "undefined" ? undefined : navigator.language)
  );
}

type LocaleCtx = { locale: Locale; setLocale: (locale: Locale) => void };

// Default value lets `useT()` work outside a provider (untouched components,
// isolated unit tests) — it renders zh-TW and `setLocale` is a no-op.
const LocaleContext = createContext<LocaleCtx>({ locale: "zh-TW", setLocale: () => {} });

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const setLocale = useCallback((next: Locale) => {
    setStoredLocale(next);
    setLocaleState(next);
  }, []);
  return <LocaleContext.Provider value={{ locale, setLocale }}>{children}</LocaleContext.Provider>;
}

/** The active locale and a sticky setter. */
export function useLocale(): [Locale, (locale: Locale) => void] {
  const { locale, setLocale } = useContext(LocaleContext);
  return [locale, setLocale];
}

/** `t(key)` bound to the active locale. */
export function useT(): (key: MsgKey) => string {
  const { locale } = useContext(LocaleContext);
  return useCallback((key: MsgKey) => translate(locale, key), [locale]);
}
