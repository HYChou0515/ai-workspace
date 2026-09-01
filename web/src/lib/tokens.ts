/**
 * Token counts for display (#739). Thousands are the unit people reason in
 * when they talk about a context window, so 20,480 reads as `20.5k` — precise
 * enough to watch it move, short enough to sit beside a composer.
 */
export function formatTokens(n: number): string {
  if (n < 1000) return String(Math.max(0, Math.round(n)));
  const k = (n / 1000).toFixed(1);
  return `${k.endsWith(".0") ? k.slice(0, -2) : k}k`;
}
