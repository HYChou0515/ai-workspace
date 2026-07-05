/**
 * Global settings (#226) — a gear in the GlobalNav opens one platform-wide
 * settings dialog. Because apps are template-driven, these are NOT per-App:
 * font size, theme and language live here (consolidated out of the per-App
 * workspace shell) so they're reachable from every App / KB / Diagnostics.
 */

import { type ReactNode, useState } from "react";

import { API_PREFIX } from "../api/http";
import { type ThemeMode, useThemeMode } from "../hooks/theme";
import { useT } from "../lib/i18n";
import { FontSizeSlider } from "./FontSizeSlider";
import { Icon } from "./Icon";
import { LanguageToggle } from "./LanguageToggle";
import { ModalShell } from "./ModalShell";

const THEME_MODES: ThemeMode[] = ["system", "light", "dark"];

export function GlobalSettings() {
  const t = useT();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        aria-label={t("settings.title")}
        title={t("settings.title")}
        onClick={() => setOpen(true)}
        style={{ display: "inline-flex", color: "var(--text-paper-d)", padding: 2 }}
      >
        <Icon name="settings" size={16} />
      </button>

      {open && (
        <ModalShell
          onClose={() => setOpen(false)}
          ariaLabel={t("settings.title")}
          width={360}
          maxWidth="calc(100vw - 32px)"
          backdropStyle={{ background: "rgba(20,22,28,0.35)", backdropFilter: "blur(2px)" }}
          panelStyle={{ padding: 20, boxShadow: "0 20px 50px rgba(20,22,28,0.25)" }}
        >
          <div style={{ display: "flex", alignItems: "center", marginBottom: 16, gap: 8 }}>
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontWeight: 700,
                  fontSize: "var(--text-display-sm)",
                  color: "var(--text-paper)",
                  flex: 1,
                }}
              >
                {t("settings.title")}
              </span>
              <button
                type="button"
                aria-label={t("settings.close")}
                onClick={() => setOpen(false)}
                style={{ color: "var(--text-paper-d)" }}
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            <Section label={t("settings.fontsize")}>
              <FontSizeSlider />
            </Section>

            <Section label={t("settings.theme")}>
              <ThemePicker />
              <p style={{ marginTop: 6, fontSize: "var(--text-xs)", color: "var(--text-paper-d)" }}>
                {t("settings.theme.note")}
              </p>
            </Section>

            <Section label={t("settings.language")}>
              <LanguageToggle />
            </Section>

            <Section label={t("settings.about")}>
              <dl
                style={{
                  margin: 0,
                  display: "grid",
                  gridTemplateColumns: "max-content 1fr",
                  rowGap: 4,
                  columnGap: 12,
                  fontSize: "var(--text-small)",
                }}
              >
                <dt style={{ color: "var(--text-paper-d)" }}>{t("about.signin")}</dt>
                <dd style={{ margin: 0 }}>{t("about.signin.value")}</dd>
                <dt style={{ color: "var(--text-paper-d)" }}>{t("about.docs")}</dt>
                <dd style={{ margin: 0 }}>
                  <a
                    href={`${API_PREFIX}/docs`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: "var(--accent-h)", textDecoration: "underline" }}
                  >
                    {t("about.docs.link")}
                  </a>
                </dd>
              </dl>
            </Section>
        </ModalShell>
      )}
    </>
  );
}

function ThemePicker() {
  const t = useT();
  const [mode, setMode] = useThemeMode();
  return (
    <div role="radiogroup" aria-label={t("settings.theme")} style={{ display: "flex", gap: 8 }}>
      {THEME_MODES.map((m) => {
        const on = mode === m;
        return (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => setMode(m)}
            style={{
              flex: 1,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 5,
              padding: "8px 10px",
              borderRadius: "var(--radius-btn)",
              border: `1px solid ${on ? "var(--accent)" : "var(--paper-3)"}`,
              background: on ? "var(--accent-soft)" : "var(--white)",
              color: on ? "var(--accent-h)" : "var(--text-paper)",
              fontWeight: on ? 600 : 400,
              fontSize: "var(--text-body-sm)",
              cursor: "pointer",
            }}
          >
            {on && <Icon name="check" size={12} />}
            {t(`theme.${m}`)}
          </button>
        );
      })}
    </div>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div
        className="caps"
        style={{ fontSize: "var(--text-xs)", color: "var(--text-paper-d2)", marginBottom: 8 }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}
