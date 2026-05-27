/** The date part of an ISO timestamp as `YYYY/MM/DD` (zero-padded, local time).
 * Used for "Opened" etc. — avoids `toLocaleDateString()`'s locale-dependent
 * US `M/D/YYYY`. */
export function ymd(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}
