/**
 * Bytes → a short human string. Sizes here span KB to tens of GB.
 *
 * Its own module because two unrelated surfaces need it: the resources page's
 * gauges, and the refusal messages that now carry the numbers behind them. A
 * lib importing a page for a pure formatter is the wrong direction.
 */
export function formatBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  const value = n / 1024 ** i;
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}
