/**
 * A page's identity controls (`docs/plan-wui-viewer-login.md` Q10–Q12).
 *
 * Two buttons, drawn by the PLATFORM and never inside a page's frame:
 *
 * * **🔑** — names what the viewer must sign in to or set ("Sign in to ERP",
 *   "Set MAP_KEY"; three or more: "Sign in to 3 systems"), and opens the
 *   platform's own panel (`ItemEnvModal`). What is typed there never reaches
 *   the page.
 * * **This page's schedules** — who each runs as, and "Run as me". Taking a
 *   schedule over from someone asks first; they are told afterwards (server).
 *
 * `PageIdentityBar` is the strip a `/w/` page gets ABOVE its frame — never
 * over it: trusted UI belongs on the line between the platform's pixels and
 * the page's (Eric Lawrence's "line of death"; Chromium's browser-UI security
 * notes), where the page can neither cover nor imitate it in place. It is drawn
 * only when there is something to say, so a page nobody signs in to keeps the
 * whole window. In the workspace pane the toolbar already IS that line, and
 * `PageIdentityControls` sits in it.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api as defaultApi } from "../api";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import {
  scheduleBindingsApi,
  type BindingRow,
  type ScheduleBindingsClient,
} from "../api/scheduleBindings";
import type { ApiClient } from "../api/types";
import { identityState, type Missing } from "../lib/identityState";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { useDialog } from "./Dialog";
import { ItemEnvModal } from "./ItemEnvModal";
import { Popover } from "./Popover";

type Clients = {
  client?: Pick<ApiClient, "getItemTools" | "getEnvProviders" | "resolveEnvProvider">;
  privateClient?: PrivateEnvClient;
  bindingsClient?: ScheduleBindingsClient;
};

type Where = { slug: string; itemId: string; folder: string };

/** The schedules file a page in ``folder`` declares its schedules in — the
 * one the sweep reads for it (`workflow/workspace_store.SCHEDULES_FILE`). */
export const schedulesPathFor = (folder: string) => `${folder.replace(/\/$/, "")}/schedules.json`;

export function usePageIdentity({
  slug,
  itemId,
  folder,
  client = defaultApi,
  privateClient = privateEnvApi,
  bindingsClient = scheduleBindingsApi,
}: Where & Clients) {
  const path = schedulesPathFor(folder);
  const layers = useQuery({
    queryKey: qk.envLayers(slug, itemId),
    queryFn: () => privateClient.layers(slug, itemId),
  });
  const tools = useQuery({
    queryKey: qk.itemTools(slug, itemId),
    queryFn: () => client.getItemTools(slug, itemId),
  });
  const providers = useQuery({
    queryKey: qk.envProviders(slug, itemId),
    queryFn: () => client.getEnvProviders(slug, itemId),
  });
  const mine = useQuery({
    queryKey: qk.privateEnv(slug, itemId),
    queryFn: () => privateClient.get(slug, itemId),
  });
  const rows = useQuery({
    queryKey: qk.scheduleBindings(slug, itemId, path),
    queryFn: () => bindingsClient.list(slug, itemId, path),
  });
  const settled = [layers, tools, providers, mine, rows].every((q) => !q.isPending);
  const state = identityState({
    tools: tools.data ?? [],
    shared: layers.data?.shared ?? {},
    policy: layers.data?.policy ?? {},
    mine: mine.data ?? {},
    providers: providers.data ?? [],
    hasSchedules: (rows.data ?? []).length > 0,
  });
  return { settled, state, rows: rows.data ?? [], path };
}

function useKeyLabel(missing: Missing[]): string {
  const t = useT();
  if (missing.length === 0) return t("env.bar.signedIn");
  if (missing.length >= 3) return t("env.bar.many", { count: String(missing.length) });
  const logins = missing.filter((m) => m.kind === "login").map((m) => m.name);
  const sets = missing.filter((m) => m.kind === "set").map((m) => m.name);
  return [
    logins.length > 0 ? t("env.bar.login", { names: logins.join("、") }) : "",
    sets.length > 0 ? t("env.bar.set", { names: sets.join("、") }) : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function PageIdentityControls(props: Where & Clients) {
  const { slug, itemId, client, privateClient, bindingsClient = scheduleBindingsApi } = props;
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const { settled, state, rows, path } = usePageIdentity(props);
  const label = useKeyLabel(state.missing);
  const [envOpen, setEnvOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const change = async (row: BindingRow) => {
    setProblem(null);
    try {
      if (row.mine) {
        await bindingsClient.unbind(slug, itemId, path, row.trigger_id);
      } else {
        if (row.bound_to) {
          const choice = await dialog.confirm({
            title: t("sched.replaceTitle"),
            body: t("sched.replaceBody", { who: row.bound_to }),
            actions: [
              { id: "cancel", label: t("env.cancel") },
              { id: "replace", label: t("sched.replaceMe"), variant: "primary" },
            ],
          });
          if (choice !== "replace") return;
        }
        await bindingsClient.bind(slug, itemId, path, row.trigger_id);
      }
    } catch (err) {
      const why = (err as { detail?: unknown })?.detail;
      setProblem(
        t("sched.failed", { why: typeof why === "string" ? why : String((err as Error).message) }),
      );
    } finally {
      await queryClient.invalidateQueries({ queryKey: qk.scheduleBindings(slug, itemId, path) });
    }
  };

  // Nothing to say ⇒ nothing drawn: most pages have no one to sign in.
  if (!settled || !state.show) return null;
  return (
    <>
      {rows.length > 0 && (
        <Popover
          align="end"
          width={340}
          trigger={({ onClick, open }) => (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="page-schedules"
              aria-expanded={open}
              onClick={onClick}
            >
              {t("env.bar.schedules")} ▾
            </button>
          )}
        >
          {() => (
            <div style={{ display: "grid", gap: 8, padding: 8 }}>
              {rows.map((row) => (
                <div
                  key={row.trigger_id}
                  data-testid={`page-schedule-${row.trigger_id}`}
                  data-bound={row.bound_to}
                  style={{ display: "grid", gap: 2 }}
                >
                  <span style={{ fontSize: pxToRem(12), fontWeight: 500 }}>
                    {row.describe ? `${row.describe} · ` : ""}
                    {row.run}
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                      {row.mine
                        ? t("sched.runsAsYou")
                        : row.bound_to
                          ? t("sched.runsAs", { who: row.bound_to })
                          : t("sched.runsAsNobody")}
                    </span>
                    <button
                      type="button"
                      className="btn"
                      data-variant="secondary"
                      data-size="sm"
                      style={{ marginLeft: "auto" }}
                      onClick={() => void change(row)}
                    >
                      {row.mine
                        ? t("sched.unbind")
                        : row.bound_to
                          ? t("sched.replaceMe")
                          : t("sched.bindMe")}
                    </button>
                  </span>
                </div>
              ))}
              {problem && (
                <p role="alert" style={{ margin: 0, fontSize: pxToRem(11), color: "var(--err)" }}>
                  {problem}
                </p>
              )}
            </div>
          )}
        </Popover>
      )}
      <button
        type="button"
        className="btn"
        data-variant={state.missing.length > 0 ? "primary" : "secondary"}
        data-size="sm"
        data-testid="page-identity-key"
        onClick={() => setEnvOpen(true)}
      >
        <span aria-hidden>🔑 </span>
        {label}
      </button>
      {envOpen && (
        <ItemEnvModal
          slug={slug}
          itemId={itemId}
          onClose={() => setEnvOpen(false)}
          client={client}
          privateClient={privateClient}
        />
      )}
    </>
  );
}

/** The `/w/` strip above a page's frame — drawn only when there is something
 * to sign in to or a schedule to run as oneself. */
export function PageIdentityBar(props: Where & Clients & { title: string }) {
  const { settled, state } = usePageIdentity(props);
  if (!settled || !state.show) return null;
  return (
    <div
      data-testid="page-identity-bar"
      style={{
        flex: "0 0 auto",
        display: "flex",
        alignItems: "center",
        gap: 8,
        minHeight: 32,
        padding: "2px 10px",
        borderBottom: "1px solid var(--paper-3)",
        background: "var(--paper)",
        // Names whose page this is: part of how a person tells the platform's
        // sign-in from a look-alike a page might draw.
        fontSize: pxToRem(12),
      }}
    >
      <span
        style={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: "var(--text-paper-d)",
        }}
      >
        {props.title}
      </span>
      <span style={{ marginLeft: "auto", display: "flex", gap: 6, flexShrink: 0 }}>
        <PageIdentityControls {...props} />
      </span>
    </div>
  );
}
