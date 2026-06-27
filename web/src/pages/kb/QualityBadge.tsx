// #105: a compact quality badge — the doc's AI grade (0–100) coloured by its
// good/ok/bad band. Un-scored docs (neutral) render nothing, so the tree stays
// clean and only *judged* docs draw a chip.

import { useT } from "../../lib/i18n";

import { qualityTone } from "./quality";

export function QualityBadge({ score }: { score?: number | null }) {
  const t = useT();
  const tone = qualityTone(score);
  if (tone === null) return null;
  return (
    <span
      className={`kb-quality kb-quality--${tone}`}
      data-testid="kb-quality-badge"
      title={t("kb.quality.badge", { score: String(score), label: t(`kb.quality.${tone}`) })}
    >
      {score}
    </span>
  );
}
