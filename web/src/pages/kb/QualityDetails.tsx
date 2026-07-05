// #105 / #460 P7+P8: the open doc's AI quality verdict, shown in the status bar
// as the coloured grade + a visible good/ok/bad label, expanding on click into a
// panel with the full (untruncated) rationale and the per-dimension breakdown.
// Replaces the old hover-only, 22-char-clipped rationale that hid the "why".

import { useState } from "react";

import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

import { QualityBadge } from "./QualityBadge";
import { qualityTone } from "./quality";

export function QualityDetails({
  score,
  rationale,
  breakdown,
}: {
  score: number;
  rationale?: string;
  breakdown?: Record<string, number>;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const tone = qualityTone(score);
  const entries = Object.entries(breakdown ?? {});
  // Nothing to expand into? Then the toggle is just noise — show the inline
  // verdict without the disclosure.
  const hasDetails = Boolean(rationale) || entries.length > 0;

  const head = (
    <>
      <span className="kb-ide__status-quality-label">{t("kb.quality.heading")}</span>
      <QualityBadge score={score} />
      {tone && (
        <span className="kb-ide__status-quality-verdict" data-testid="kb-quality-verdict">
          {t(`kb.quality.${tone}`)}
        </span>
      )}
    </>
  );

  return (
    <span
      className="kb-ide__status-quality"
      data-testid="kb-ide-quality"
      style={{ position: "relative" }}
    >
      {hasDetails ? (
        <button
          type="button"
          className="kb-ide__status-quality-toggle"
          data-testid="kb-quality-details-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {head}
          <Icon name={open ? "chev_d" : "chev_r"} size={11} />
        </button>
      ) : (
        head
      )}

      {open && hasDetails && (
        <div className="kb-ide__quality-panel" data-testid="kb-quality-panel">
          {rationale && (
            <p className="kb-ide__quality-rationale" data-testid="kb-quality-rationale">
              {rationale}
            </p>
          )}
          {entries.length > 0 && (
            <dl
              className="kb-ide__quality-breakdown"
              data-testid="kb-quality-breakdown"
              style={{ margin: 0, display: "grid", gap: 4 }}
            >
              {entries.map(([dim, val]) => (
                <div
                  key={dim}
                  data-testid={`kb-quality-dim-${dim}`}
                  style={{ display: "flex", alignItems: "center", gap: 8 }}
                >
                  <dt style={{ flex: 1, minWidth: 0 }}>{dim}</dt>
                  <dd
                    style={{ margin: 0, fontVariantNumeric: "tabular-nums", fontSize: pxToRem(12) }}
                  >
                    {val}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
    </span>
  );
}
