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
import { useState } from "react";
import { Link } from "react-router-dom";

import { useT } from "../lib/i18n";

import { type MyResources, type MyResourcesApi, type OverrideList as OverrideListDTO, myResourcesApi } from "../api/myResources";
import { qk } from "../api/queryKeys";
import { useIsSuperuser } from "../hooks/useIsSuperuser";
import { UserPicker } from "../components/UserPicker";

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

/** One dimension: what it is, how much of it you hold, and how close that is to
 * your ceiling.
 *
 * An UNLIMITED dimension still shows its usage — with no denominator and no bar,
 * because there is nothing to be a fraction of. It used to be hidden entirely,
 * so on a deploy that caps only the environment count you could not find out how
 * much cpu or memory you were holding at all. */
function Gauge({
  label,
  used,
  limit,
  format,
}: {
  label: string;
  used: number;
  limit: number;
  format: (n: number) => string;
}) {
  const t = useT();
  return (
    <div className="gauge">
      <p className="summary">
        <span className="gauge-label">{label}</span>
        <span className="gauge-value">{formatAgainstLimit(used, limit, format)}</span>
        {limit ? null : <span className="detail">{t("resources.gauge.unlimited")}</span>}
      </p>
      <Meter used={used} limit={limit} />
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
        {/* Three dimensions, three gauges. They used to share one line and ONE
            bar — and that bar tracked only `count`, so at "1 / 2 · CPU 1 / 4" it
            sat at 50% while cpu was at 25%: a reader takes the bar as "how full
            am I" and it answered one dimension of three. Any of them can refuse
            a turn on its own, so each shows its own. */}
        <Gauge
          label={t("resources.gauge.count")}
          used={data.live.length}
          limit={limits.count}
          format={(n) => t("resources.live.count", { n })}
        />
        <Gauge
          label={t("resources.gauge.cpu")}
          used={data.cpu_in_use}
          limit={limits.cpu}
          format={(n) => `${n}`}
        />
        <Gauge
          label={t("resources.memory")}
          used={data.memory_in_use}
          limit={limits.memory_bytes}
          format={formatBytes}
        />
        {data.live.length === 0 ? (
          <p className="empty">{t("resources.live.empty")}</p>
        ) : (
          <ul>
            {data.live.map((env) => (
              <li key={env.item_id}>
                <Link to={`/a/${env.slug}/${env.item_id}`}>{env.title || env.item_id}</Link>
                <span className="detail">
                  {env.cpu_cores
                    ? t(env.cpu_cores === 1 ? "resources.live.cores_one" : "resources.live.cores", {
                        n: env.cpu_cores,
                      })
                    : ""}
                  {env.memory_bytes ? ` · ${formatBytes(env.memory_bytes)}` : ""}
                </span>
                <button
                  type="button"
                  className="btn"
                  data-variant="secondary"
                  data-size="sm"
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
        {!data.disk_tracked ? (
          // Not "0 B" — this deploy caps nobody's disk, so nothing is measured.
          // Reporting zero would state something false on a page anyone can open.
          <p className="empty">{t("resources.disk.untracked")}</p>
        ) : (
          <>
            <Gauge
              label={t("resources.disk.heading")}
              used={data.disk_in_use}
              limit={limits.disk_bytes}
              format={formatBytes}
            />
          </>
        )}
        {!data.disk_tracked ? null : data.workspaces.length === 0 ? (
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
        {data.disk_tracked ? <p className="hint">{t("resources.disk.hint")}</p> : null}
      </section>

      <AdminOverrides client={client} />
    </div>
  );
}

/**
 * Raise one person above the site default (#688 P7). Superuser only — and the
 * backend answers 404, not 403, to everyone else, so this section simply does
 * not render rather than offering a control that would be refused.
 *
 * The form starts EMPTY on purpose and does not pre-fill from the person's
 * current numbers. `PUT` is replace-semantics — it rewrites all four dimensions
 * — and the read endpoint returns EFFECTIVE limits (override merged over the
 * deploy default), so pre-filling would submit inherited values back as explicit
 * overrides and quietly pin them: a later change to the site default would then
 * skip everyone who had ever been edited here. Blank means "keep the default",
 * which is the same thing the backend's own 0/"" sentinel means.
 */
function AdminOverrides({ client }: { client: MyResourcesApi }) {
  const t = useT();
  const isSuperuser = useIsSuperuser();
  const [userId, setUserId] = useState("");
  const [form, setForm] = useState({ count: "", cpu: "", memory: "", disk: "" });
  const [looked, setLooked] = useState<MyResources | null | undefined>(undefined);
  const [saved, setSaved] = useState(false);
  const list = useQuery({
    queryKey: qk.userOverrides,
    queryFn: () => client.adminList(),
    enabled: isSuperuser,
  });

  if (!isSuperuser) return null;

  const refreshList = () => void list.refetch();

  const lookup = async () => {
    setSaved(false);
    setLooked(userId ? await client.adminGet(userId) : undefined);
  };
  const save = async () => {
    await client.adminSet(userId, {
      count: form.count ? Number(form.count) : 0,
      cpu: form.cpu ? Number(form.cpu) : 0,
      memory: form.memory,
      disk: form.disk,
    });
    setSaved(true);
    setLooked(await client.adminGet(userId));
    refreshList();
  };
  const clear = async () => {
    await client.adminClear(userId);
    setForm({ count: "", cpu: "", memory: "", disk: "" });
    setSaved(true);
    setLooked(await client.adminGet(userId));
    refreshList();
  };

  const clearOne = async (u: string) => {
    await client.adminClear(u);
    refreshList();
    if (u === userId) setLooked(await client.adminGet(u));
  };

  const amount = (n: number, fmt: (v: number) => string) => (n ? fmt(n) : t("resources.admin.unlimited"));

  return (
    <section className="admin" aria-labelledby="admin-heading">
      <h2 id="admin-heading">{t("resources.admin.heading")}</h2>
      <p className="hint">{t("resources.admin.intro")}</p>

      {list.data ? <OverrideList data={list.data} onClear={(u) => void clearOne(u)} /> : null}

      {/* Picked from the directory, not typed. An exception is granted to a
          PERSON, and a mistyped id saves perfectly well — it lands in the list
          and binds to nobody, so the operator sees an allowance that looks
          granted and never applies. The picker is the same one share/@mention
          use, so "who exists" has one answer across the app. */}
      <div className="admin-row">
        <span className="admin-field">
          <label id="q-user-label">{t("resources.admin.user")}</label>
          <UserPicker
            selected={userId ? [userId] : []}
            onToggle={(id) => {
              // Single-select: picking someone else REPLACES the target rather
              // than adding to it, and picking the same person again clears it.
              setUserId((prev) => (prev === id ? "" : id));
              setLooked(undefined);
              setSaved(false);
            }}
            placeholder={t("resources.admin.userSearch")}
          />
        </span>
        <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={() => void lookup()}>
          {t("resources.admin.lookup")}
        </button>
      </div>

      {looked === null ? <p className="hint">{t("resources.admin.notfound")}</p> : null}
      {looked ? (
        <p className="hint">
          {t("resources.admin.effective")}: {t("resources.admin.count")} {amount(looked.limits.count, String)} ·{" "}
          {t("resources.admin.cpu")} {amount(looked.limits.cpu, String)} · {t("resources.admin.memory")}{" "}
          {amount(looked.limits.memory_bytes, formatBytes)} · {t("resources.admin.disk")}{" "}
          {amount(looked.limits.disk_bytes, formatBytes)}
        </p>
      ) : null}

      <div className="admin-row">
        <span className="admin-field">
          <label htmlFor="q-count">{t("resources.admin.count")}</label>
          <input
            id="q-count"
            type="number"
            min="0"
            value={form.count}
            onChange={(e) => setForm({ ...form, count: e.target.value })}
          />
        </span>
        <span className="admin-field">
          <label htmlFor="q-cpu">{t("resources.admin.cpu")}</label>
          <input
            id="q-cpu"
            type="number"
            min="0"
            step="0.5"
            value={form.cpu}
            onChange={(e) => setForm({ ...form, cpu: e.target.value })}
          />
        </span>
        <span className="admin-field">
          <label htmlFor="q-mem">{t("resources.admin.memory")}</label>
          <input
            id="q-mem"
            placeholder="8G"
            value={form.memory}
            onChange={(e) => setForm({ ...form, memory: e.target.value })}
          />
        </span>
        <span className="admin-field">
          <label htmlFor="q-disk">{t("resources.admin.disk")}</label>
          <input
            id="q-disk"
            placeholder="50G"
            value={form.disk}
            onChange={(e) => setForm({ ...form, disk: e.target.value })}
          />
        </span>
      </div>

      <div className="admin-row">
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-size="sm"
          disabled={!userId}
          onClick={() => void save()}
        >
          {t("resources.admin.save")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          disabled={!userId}
          onClick={() => void clear()}
        >
          {t("resources.admin.clear")}
        </button>
        {saved ? <span className="detail">{t("resources.admin.saved")}</span> : null}
      </div>
    </section>
  );
}

/**
 * Who is above the baseline, and the baseline itself.
 *
 * The by-id lookup below can only confirm an exception you already suspect, so
 * without this an operator inheriting the system cannot answer "who has one?".
 * Rows show the RAW override — only the dimensions actually granted — because
 * merging them against the default would make every row look overridden in
 * every dimension, which is the opposite of what this list is for.
 */
function OverrideList({
  data,
  onClear,
}: {
  data: OverrideListDTO;
  onClear: (userId: string) => void;
}) {
  const t = useT();
  const d = data.defaults;
  const dims = (o: OverrideListDTO["overrides"][number]) =>
    [
      o.count ? `${t("resources.admin.count")} ${o.count}` : "",
      o.cpu ? `${t("resources.admin.cpu")} ${o.cpu}` : "",
      o.memory ? `${t("resources.admin.memory")} ${o.memory}` : "",
      o.disk ? `${t("resources.admin.disk")} ${o.disk}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  const def = (n: number, fmt: (v: number) => string) =>
    n ? fmt(n) : t("resources.admin.unlimited");

  return (
    <>
      <p className="hint">
        {t("resources.admin.defaults")}: {t("resources.admin.count")} {def(d.count, String)} ·{" "}
        {t("resources.admin.cpu")} {def(d.cpu, String)} · {t("resources.admin.memory")}{" "}
        {def(d.memory_bytes, formatBytes)} · {t("resources.admin.disk")}{" "}
        {def(d.disk_bytes, formatBytes)}
      </p>
      <h3 className="admin-sub">{t("resources.admin.who")}</h3>
      {data.overrides.length === 0 ? (
        <p className="empty">{t("resources.admin.none")}</p>
      ) : (
        <ul>
          {data.overrides.map((o) => (
            <li key={o.user_id}>
              <span className="who">{o.user_id}</span>
              <span className="detail">{dims(o)}</span>
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                onClick={() => onClear(o.user_id)}
              >
                {t("resources.admin.clear")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
