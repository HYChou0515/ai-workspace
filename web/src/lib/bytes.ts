/**
 * Human-readable byte sizes for the workspace usage bar + file-size displays
 * (#245). Binary units (1 KB = 1024 B); whole bytes under 1 KB, one decimal
 * above so "20.0 GB" reads cleanly.
 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(1)} ${units[i]}`;
}
