/**
 * A page's identity controls (`docs/plan-wui-viewer-login.md` Q10–Q12).
 *
 * Two buttons, drawn by the PLATFORM and never inside a page's frame:
 *
 * * **The key button** (the Env button's own icon) — names what the viewer must sign in to or set ("Sign in to ERP",
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api as defaultApi } from "../api";
import { personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import {
  scheduleBindingsApi,
  type BindingRow,
  type ScheduleBindingsClient,
} from "../api/scheduleBindings";
import type { ApiClient } from "../api/types";
import { ownLayer } from "../lib/envLayers";
import { identityState, keyLabelParts, type Missing } from "../lib/identityState";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { useDialog } from "./Dialog";
import { ItemEnvModal } from "./ItemEnvModal";
import { Icon } from "./Icon";

type Clients = {
  client?: Pick<ApiClient, "getItemTools" | "getEnvProviders" | "resolveEnvProvider">;
  privateClient?: PrivateEnvClient;
  /** My environment variables (`plan-personal-env`). */
  personalClient?: Pick<PersonalEnvClient, "get" | "put">;
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
  personalClient = personalEnvApi,
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
  const personal = useQuery({ queryKey: qk.personalEnv(), queryFn: () => personalClient.get() });
  const settled = [layers, tools, providers, mine, rows, personal].every((q) => !q.isPending);
  const state = identityState({
    tools: tools.data?.tools ?? [],
    shared: layers.data?.shared ?? {},
    policy: layers.data?.policy ?? {},
    // What the deploy filled in wins a name over what they typed.
    mine: ownLayer(mine.data?.values ?? {}, mine.data?.auto ?? {}),
    personal: personal.data?.values ?? {},
    providers: providers.data ?? [],
    hasSchedules: (rows.data ?? []).length > 0,
  });
  return { settled, state, rows: rows.data ?? [], path };
}

type KeyState = "missing" | "signedIn" | "yours";

function useKeyLabel(missing: Missing[], holdsOwn: boolean): { state: KeyState; label: string } {
  const t = useT();
  if (missing.length === 0) {
    // "Signed in" only when it is true — nothing missing is not the same as
    // holding anything of one's own.
    return holdsOwn
      ? { state: "signedIn", label: t("env.bar.signedIn") }
      : { state: "yours", label: t("env.bar.yours") };
  }
  return { state: "missing", label: missingLabel(t, missing) };
}

export function missingLabel(t: ReturnType<typeof useT>, missing: Missing[]): string {
  const parts = keyLabelParts(missing);
  const sep = t("env.bar.sep");
  if (parts.kind === "manySystems") return t("env.bar.many", { count: String(parts.count) });
  if (parts.kind === "manyItems")
    return t("env.bar.manyItems", { names: parts.first.join(sep), count: String(parts.count) });
  return [
    parts.logins.length > 0 ? t("env.bar.login", { names: parts.logins.join(sep) }) : "",
    parts.sets.length > 0 ? t("env.bar.set", { names: parts.sets.join(sep) }) : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function PageIdentityControls(props: Where & Clients) {
  const {
    slug,
    itemId,
    client,
    privateClient,
    personalClient,
    bindingsClient = scheduleBindingsApi,
  } = props;
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const { settled, state, rows, path } = usePageIdentity(props);
  const key = useKeyLabel(state.missing, state.holdsOwn);
  const [envOpen, setEnvOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const change = async (row: BindingRow) => {
    setProblem(null);
    if (!row.mine && row.bound_to) {
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
    toggle.mutate(row);
  };
  // A mutation, not a bare await: a failure also reaches the app's one write-
  // failure notice (MutationCache.onError), not only this panel's line.
  const toggle = useMutation({
    mutationFn: (row: BindingRow) =>
      row.mine
        ? bindingsClient.unbind(slug, itemId, path, row.trigger_id)
        : bindingsClient.bind(slug, itemId, path, row.trigger_id),
    onError: (err) => {
      const why = (err as { detail?: unknown })?.detail;
      setProblem(
        t("sched.failed", { why: typeof why === "string" ? why : String((err as Error).message) }),
      );
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: qk.scheduleBindings(slug, itemId, path) }),
  });
  // Nothing to say ⇒ nothing drawn: most pages have no one to sign in.
  if (!settled || !state.show) return null;
  return (
    <>
      {rows.length > 0 && (
        <SchedulesButton t={t}>
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
        </SchedulesButton>
      )}
      <button
        type="button"
        className="btn"
        data-variant={state.missing.length > 0 ? "primary" : "secondary"}
        data-size="sm"
        data-testid="page-identity-key"
        data-state={key.state}
        onClick={() => setEnvOpen(true)}
        style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
      >
        {/* The Env button's own icon — this opens the same panel. An emoji
            key rendered as an empty box where the font had none. */}
        <Icon name="tag" size={12} />
        {key.label}
      </button>
      {envOpen && (
        <ItemEnvModal
          slug={slug}
          itemId={itemId}
          onClose={() => setEnvOpen(false)}
          client={client}
          privateClient={privateClient}
          personalClient={personalClient}
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

/** The schedules list, in a panel clamped to the viewport. The shared
 * `Popover` pins a fixed-width box to one edge of its trigger; with the trigger
 * mid-strip on a 390px phone NEITHER edge fits and it ran off the screen (seen
 * in a real browser). This one opens below the button, right-aligned to it,
 * and slides left as far as it must to stay on screen. */
function SchedulesButton({ t, children }: { t: ReturnType<typeof useT>; children: ReactNode }) {
  const [at, setAt] = useState<{ top: number; left: number; width: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!at) return;
    const onDoc = (e: MouseEvent) => {
      const n = e.target as Node;
      if (!panel.current?.contains(n) && !button.current?.contains(n)) setAt(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAt(null);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [at]);
  const toggle = () => {
    if (at) return setAt(null);
    const r = button.current?.getBoundingClientRect();
    const vw = window.innerWidth;
    const margin = 8;
    const width = Math.min(340, vw - 2 * margin);
    const right = r ? r.right : vw - margin;
    const left = Math.max(margin, Math.min(right - width, vw - margin - width));
    setAt({ top: (r ? r.bottom : 0) + 6, left, width });
  };
  return (
    <>
      <button
        ref={button}
        type="button"
        className="btn"
        data-variant="secondary"
        data-size="sm"
        data-testid="page-schedules"
        aria-expanded={at !== null}
        onClick={toggle}
      >
        {t("env.bar.schedules")} ▾
      </button>
      {at && (
        <div
          ref={panel}
          role="dialog"
          aria-label={t("env.bar.schedules")}
          data-testid="page-schedules-panel"
          style={{
            position: "fixed",
            top: at.top,
            left: at.left,
            width: at.width,
            background: "var(--white)",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-card)",
            boxShadow: "0 6px 20px rgba(20,22,28,0.08)",
            zIndex: "var(--z-popover)",
          }}
        >
          {children}
        </div>
      )}
    </>
  );
}

/** What the viewer still has to provide for this item's tools — for an entry
 * point that is not a page (the chat's Env button). No schedules here: a chat
 * has none of its own. */
export function useEnvMissing({
  slug,
  itemId,
  shared,
  policy,
  enabled,
  client = defaultApi,
  privateClient = privateEnvApi,
  personalClient = personalEnvApi,
}: {
  slug: string;
  itemId: string;
  shared: Record<string, string>;
  policy: Record<string, string>;
  enabled: boolean;
  client?: Pick<ApiClient, "getItemTools" | "getEnvProviders">;
  privateClient?: Pick<PrivateEnvClient, "get">;
  personalClient?: Pick<PersonalEnvClient, "get">;
}): Missing[] {
  const on = enabled && Boolean(slug && itemId);
  const tools = useQuery({
    queryKey: qk.itemTools(slug, itemId),
    queryFn: () => client.getItemTools(slug, itemId),
    enabled: on,
  });
  const providers = useQuery({
    queryKey: qk.envProviders(slug, itemId),
    queryFn: () => client.getEnvProviders(slug, itemId),
    enabled: on,
  });
  const mine = useQuery({
    queryKey: qk.privateEnv(slug, itemId),
    queryFn: () => privateClient.get(slug, itemId),
    enabled: on,
  });
  const personal = useQuery({
    queryKey: qk.personalEnv(),
    queryFn: () => personalClient.get(),
    enabled: on,
  });
  if (!on) return [];
  return identityState({
    tools: tools.data?.tools ?? [],
    shared,
    policy,
    mine: ownLayer(mine.data?.values ?? {}, mine.data?.auto ?? {}),
    personal: personal.data?.values ?? {},
    providers: providers.data ?? [],
    hasSchedules: false,
  }).missing;
}
