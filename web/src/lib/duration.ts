import { useT } from "./i18n";

/** A duration in the unit that reads best: seconds under a minute, minutes
 * under an hour, hours (one decimal) above. The unit is chosen AFTER rounding,
 * so 59.6 s reads "1 min", never "60 s". */
export function durationParts(
  ms: number,
): { unit: "seconds" | "minutes"; n: number } | { unit: "hours"; n: string } {
  const s = Math.round(ms / 1000);
  if (s < 60) return { unit: "seconds", n: s };
  const m = Math.round(s / 60);
  if (m < 60) return { unit: "minutes", n: m };
  return { unit: "hours", n: (s / 3600).toFixed(1) };
}

/** `durationParts`, worded (`wui.usage.seconds` / `minutes` / `hours`). */
export function useDuration(): (ms: number) => string {
  const t = useT();
  return (ms) => {
    const d = durationParts(ms);
    return t(`wui.usage.${d.unit}`, { n: d.n });
  };
}
