/**
 * Settings-panel language switcher (#160). A segmented control mirroring the
 * Theme buttons; the two options are endonyms (shown in their own script, the
 * same in either locale), so they're not routed through `t()`.
 */

import { useLocale, type Locale } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

const OPTIONS: { id: Locale; label: string }[] = [
  { id: "zh-TW", label: "中文" },
  { id: "en", label: "English" },
];

export function LanguageToggle() {
  const [locale, setLocale] = useLocale();
  return (
    <div style={{ display: "flex", gap: 6 }}>
      {OPTIONS.map((o) => {
        const on = o.id === locale;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            onClick={() => setLocale(o.id)}
            style={{
              padding: "6px 12px",
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-btn)",
              fontSize: pxToRem(12),
              background: on ? "var(--accent-soft)" : "var(--white)",
              color: on ? "var(--accent-h)" : "var(--text-paper)",
              cursor: "pointer",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
