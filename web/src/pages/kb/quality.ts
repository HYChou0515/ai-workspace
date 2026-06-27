// #105: present a document's AI quality score (0–100) as a coarse good/ok/bad
// band for the badge + status bar. The numeric score is what the backend stores
// and what the retriever uses; the band is purely presentational.

export type QualityTone = "good" | "ok" | "bad";

/** Map a 0–100 quality score to a coarse tone. `null`/`undefined` ⇒ un-scored
 * (neutral) → `null`, so callers render no badge. Thresholds: ≥70 good,
 * ≥40 ok, else bad. */
export function qualityTone(score: number | null | undefined): QualityTone | null {
  if (score == null) return null;
  if (score >= 70) return "good";
  if (score >= 40) return "ok";
  return "bad";
}
