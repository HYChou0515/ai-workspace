/**
 * System font-size control (#226). A range slider over the document-root scale
 * (85%–150%, 5% steps). Dragging live-scales the whole UI — every rem grows —
 * via the shared FontScale context, and the value persists. A reset returns to
 * the 100% default. Spacing stays fixed, so the layout never breaks the way
 * browser zoom does.
 */

import { FONT_SCALE_DEFAULT, FONT_SCALE_MAX, FONT_SCALE_MIN, FONT_SCALE_STEP, useFontScale } from "../hooks/fontScale";
import { useT } from "../lib/i18n";
import { Icon } from "./Icon";

export function FontSizeSlider() {
  const t = useT();
  const [scale, setScale] = useFontScale();
  const pct = Math.round(scale * 100);
  const atDefault = scale === FONT_SCALE_DEFAULT;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <input
          type="range"
          aria-label={t("settings.fontsize")}
          min={Math.round(FONT_SCALE_MIN * 100)}
          max={Math.round(FONT_SCALE_MAX * 100)}
          step={Math.round(FONT_SCALE_STEP * 100)}
          value={pct}
          onChange={(e) => setScale(Number(e.target.value) / 100)}
          style={{ flex: 1, accentColor: "var(--accent)", cursor: "pointer" }}
        />
        <span
          style={{
            minWidth: 44,
            textAlign: "right",
            fontVariantNumeric: "tabular-nums",
            fontSize: "var(--text-body-sm)",
            color: "var(--text-paper)",
          }}
        >
          {pct}%
        </span>
        <button
          type="button"
          aria-label={t("settings.fontsize.reset")}
          title={t("settings.fontsize.reset")}
          onClick={() => setScale(FONT_SCALE_DEFAULT)}
          disabled={atDefault}
          style={{
            display: "inline-flex",
            color: atDefault ? "var(--text-paper-d2)" : "var(--text-paper-d)",
            cursor: atDefault ? "default" : "pointer",
            opacity: atDefault ? 0.5 : 1,
          }}
        >
          <Icon name="undo" size={14} />
        </button>
      </div>
      <p style={{ marginTop: 6, fontSize: "var(--text-xs)", color: "var(--text-paper-d)" }}>
        {t("settings.fontsize.note")}
      </p>
    </div>
  );
}
