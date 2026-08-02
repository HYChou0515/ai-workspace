/**
 * "My resource usage" — the way out of being at your limit.
 *
 * The backend refuses outright rather than evicting anything, which is only a
 * defensible choice if the person refused has somewhere to go. This page is that
 * place: two lists, each with the action that frees the thing it lists.
 *
 * The split mirrors the two kinds of resource, because the actions differ:
 * a live environment is CLOSED (cpu/memory come back at once), while stored
 * bytes are DELETED (and deleting is never quota-gated, so it always works —
 * that is what makes an over-limit state recoverable).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useT } from "../lib/i18n";

import { type MyResourcesApi, myResourcesApi } from "../api/myResources";
import { qk } from "../api/queryKeys";

/** Bytes → a short human string. Sizes here span KB to tens of GB. */
export function formatBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  const value = n / 1024 ** i;
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

/** `used of limit`, or just `used` when the dimension is unlimited (limit 0). */
export function formatAgainstLimit(
  used: number,
  limit: number,
  render: (n: number) => string,
): string {
  return limit ? `${render(used)} / ${render(limit)}` : render(used);
}

function Meter({ used, limit }: { used: number; limit: number }) {
  if (!limit) return null;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return (
    <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="meter-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function MyResourcesPage({ client = myResourcesApi }: { client?: MyResourcesApi }) {
  const t = useT();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: qk.myResources,
    queryFn: () => client.get(),
  });

  const close = useMutation({
    mutationFn: (itemId: string) => client.closeEnvironment(itemId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.myResources }),
  });

  if (isLoading || !data) return <p>{t("resources.loading")}</p>;

  const { limits } = data;
  return (
    <div className="page">
      <h1>{t("resources.heading")}</h1>

      <section aria-labelledby="live-heading">
        <h2 id="live-heading">{t("resources.live.heading")}</h2>
        <p className="summary">
          {formatAgainstLimit(data.live.length, limits.count, (n) => t("resources.live.count", { n }))}
          {limits.cpu ? ` · CPU ${formatAgainstLimit(data.cpu_in_use, limits.cpu, (n) => `${n}`)}` : ""}
          {limits.memory_bytes
            ? ` · ${t("resources.memory")} ${formatAgainstLimit(data.memory_in_use, limits.memory_bytes, formatBytes)}`
            : ""}
        </p>
        <Meter used={data.live.length} limit={limits.count} />
        {data.live.length === 0 ? (
          <p className="empty">{t("resources.live.empty")}</p>
        ) : (
          <ul>
            {data.live.map((env) => (
              <li key={env.item_id}>
                <Link to={`/a/${env.slug}/${env.item_id}`}>{env.title || env.item_id}</Link>
                <span className="detail">
                  {env.cpu_cores ? t("resources.live.cores", { n: env.cpu_cores }) : ""}
                  {env.memory_bytes ? ` · ${formatBytes(env.memory_bytes)}` : ""}
                </span>
                <button
                  type="button"
                  onClick={() => close.mutate(env.item_id)}
                  disabled={close.isPending}
                >
                  {t("resources.live.close")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="disk-heading">
        <h2 id="disk-heading">{t("resources.disk.heading")}</h2>
        <p className="summary">
          {formatAgainstLimit(data.disk_in_use, limits.disk_bytes, formatBytes)}
        </p>
        <Meter used={data.disk_in_use} limit={limits.disk_bytes} />
        {data.workspaces.length === 0 ? (
          <p className="empty">{t("resources.disk.empty")}</p>
        ) : (
          <ul>
            {data.workspaces.map((ws) => (
              <li key={ws.item_id}>
                {/* Deleting happens in the item's own file view: it is the only
                    place that shows WHAT is being deleted, and deleting the
                    wrong thing here would be unrecoverable. */}
                <Link to={`/a/${ws.slug}/${ws.item_id}`}>{ws.title || ws.item_id}</Link>
                <span className="detail">{formatBytes(ws.bytes_used)}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="hint">{t("resources.disk.hint")}</p>
      </section>
    </div>
  );
}
