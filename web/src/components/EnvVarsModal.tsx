/**
 * The per-item environment variables panel — one tab per layer, named as the
 * user named them (`docs/plan-wui-viewer-login.md`, `docs/plan-personal-env.md`
 * A20). What is done in a tab, a sign-in included, lands in that tab's layer
 * and nowhere else:
 *
 * * **Shared** — the item's SHARED `env_vars` plus a per-variable POLICY
 *   saying which layer a tool gets: `shared_first` (the default, and what every
 *   item did before), `private_first`, `private_only`. Written by whoever holds
 *   `write_meta`; everyone else sees it read-only, with the reason.
 * * **Private** (the default tab) — the viewer's PRIVATE values for this item:
 *   typed, signed in for, or written for them by the deploy's seam. Each row
 *   says whose value a tool gets, my values for every item included.
 * * **Private(跨workspace)** — the viewer's values for every item, the same
 *   row as the "My environment variables" page: the names this item's tools
 *   need and the ones it sets to Private first / Private only, each saying
 *   whether this item uses it (only such a name does, D2). A sign-in here is
 *   stored at once.
 *
 * Only the viewer can read the private two (`api/privateEnv.ts`,
 * `api/personalEnv.ts`), so they are masked until asked.
 *
 * One layer is edited at a time, each tab saving on its own: Postman retired
 * editing a shared and a local value side by side in one row, and VS Code
 * separates User / Workspace the same way. Every Private row says whose value
 * is in use and why (`lib/envLayers.ts`, held to the backend's rule by a shared
 * table).
 *
 * The tools are SECTIONS, not a dropdown: a dropdown hid which tool needed
 * attention and showed one at a time. Each section is headed by one of three
 * states in symbol + colour + words (Carbon; never colour alone), most urgent
 * first, and only the ones missing a required value start unfolded. A tool
 * that declared nothing gets no section — a UI-layer choice: the provider
 * owes the declaration (`envNeeds.undeclared` still records it).
 *
 * The `.env` text box stays for what people actually do with these — paste a
 * block from elsewhere — folded under the list, and still the ONE copy the
 * shared fields edit (`setEnvValue`, in place, so a keystroke in a field never
 * costs the comments written in the box). It holds values only; a policy is
 * set on the variable's row.
 *
 * Shared values are not masked: they are a plain field on the item record,
 * returned unredacted to anyone with `read_meta`, so a mask would hide them
 * from the one person who may edit them and from nobody else.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { api as defaultApi } from "../api";
import { PERSONAL_ENV_WRITES, personalEnvApi, type PersonalEnvClient } from "../api/personalEnv";
import { privateEnvApi, type PrivateEnvClient } from "../api/privateEnv";
import { qk } from "../api/queryKeys";
import type { ApiClient, EnvProvider } from "../api/types";
import type { EnvTarget } from "../hooks/chatItem";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { mergeEnv, parseEnvText, setEnvValue, toEnvText, unstorable } from "../lib/envFile";
import { isBlank, layerInUse, ownLayer, policyOf, POLICIES, type EnvPolicy } from "../lib/envLayers";
import { deriveEnvNeeds, type EnvField, type SectionStatus, type ToolSection } from "../lib/envNeeds";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { sameShape } from "../lib/sameShape";
import { useDialog } from "./Dialog";
import { ModalShell } from "./ModalShell";
import { Tabs } from "./ShareTabs";

type Tab = "shared" | "mine" | "personal";
const TABS: Tab[] = ["shared", "mine", "personal"];
/** The tab each layer is edited on: "in use" names the tab, not the layer. */
const TAB_OF = { shared: "shared", private: "mine", personal: "personal" } as const;

const MONO = { fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: pxToRem(12) };
const MUTED = { fontSize: pxToRem(11), color: "var(--text-paper-d)" } as const;

/** The three section states: symbol, colour and words together. Amber, not
 * the error red — a missing required value is a hint (#750), Save always works. */
const STATUS_LOOK: Record<SectionStatus, { mark: string; color: string }> = {
  missingRequired: { mark: "◆", color: "var(--warn)" },
  missingOptional: { mark: "◇", color: "var(--text-paper-d)" },
  ready: { mark: "✓", color: "var(--ok)" },
};

export function EnvVarsModal({
  envVars,
  envPolicy = {},
  onSave,
  onClose,
  slug,
  itemId,
  client = defaultApi,
  privateClient = privateEnvApi,
  personalClient = personalEnvApi,
  target = null,
}: {
  envVars: Record<string, string>;
  envPolicy?: Record<string, string>;
  /** Store the SHARED values and policy. Absent ⇒ the caller may not
   * (`write_meta`), and the Shared tab is read-only. */
  onSave?: (
    next: Record<string, string>,
    policy: Record<string, string>,
  ) => void | boolean | Promise<void | boolean>;
  onClose: () => void;
  /** The item. Without one there is no private layer (and no declared tools):
   * only the Shared tab is drawn. */
  slug?: string;
  itemId?: string;
  client?: Pick<ApiClient, "getItemTools" | "getEnvProviders" | "resolveEnvProvider">;
  privateClient?: Pick<PrivateEnvClient, "get" | "put" | "clear">;
  /** My environment variables (`plan-personal-env`): read to say whose value
   * is in use, and edited on the cross-workspace tab. */
  personalClient?: Pick<PersonalEnvClient, "get" | "put">;
  /** Opened from a `request_env` card (docs/plan-env-request-card.md): on the
   * person's own tab (N4), at its variable — drawn even when no tool declared
   * it (D5). */
  target?: EnvTarget | null;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const hasItem = Boolean(slug && itemId);
  const [tab, setTab] = useState<Tab>(hasItem ? "mine" : "shared");
  const [query, setQuery] = useState("");

  const toolsQ = useQuery({
    queryKey: qk.itemTools(slug ?? "", itemId ?? ""),
    queryFn: () => client.getItemTools(slug!, itemId!),
    enabled: hasItem,
  });
  const providersQ = useQuery({
    queryKey: qk.envProviders(slug ?? "", itemId ?? ""),
    queryFn: () => client.getEnvProviders(slug!, itemId!),
    enabled: hasItem,
  });
  const mineQ = useQuery({
    queryKey: qk.privateEnv(slug ?? "", itemId ?? ""),
    queryFn: () => privateClient.get(slug!, itemId!),
    enabled: hasItem,
  });
  const personalQ = useQuery({
    queryKey: qk.personalEnv(),
    queryFn: () => personalClient.get(),
    enabled: hasItem,
  });
  const personal = personalQ.data?.values ?? {};
  // Only the names edited on the cross-workspace tab, "" = remove: the rest of
  // the row is the server's, so a save writes these and nothing it did not
  // touch, onto a fresh read (round 1, F2). The values are the one copy —
  // NOT the `.env` text, which trims and cannot hold a line break (review
  // A22: making the text the copy ate typed spaces and rewrote stored values).
  const [personalEdits, setPersonalEdits] = useState<Record<string, string>>({});
  const personalValues = Object.fromEntries(
    Object.entries({ ...personal, ...personalEdits }).filter(([, v]) => v !== ""),
  );
  // A name edited back to what is stored, or cleared when nothing is, changes
  // nothing.
  const personalChange = () =>
    Object.fromEntries(Object.entries(personalEdits).filter(([n, v]) => v !== (personal[n] ?? "")));

  // ── the SHARED layer: the box's text is the one copy of the values ────────
  const [text, setText] = useState(() => toEnvText(envVars));
  const [policy, setPolicy] = useState<Record<string, string>>(() => ({ ...envPolicy }));
  const shared = parseEnvText(text);
  const setVar = (name: string, value: string) => setText(setEnvValue(text, name, value));
  const choosePolicy = (name: string, p: EnvPolicy) =>
    setPolicy((prev) => {
      const next = { ...prev };
      // The default is stored as ABSENCE, so an item nobody touched stays
      // byte-identical to one from before policies existed.
      if (p === "shared_first") delete next[name];
      else next[name] = p;
      return next;
    });

  // ── the PRIVATE layer ─────────────────────────────────────────────────────
  // The same shape as the cross-workspace tab: only the names edited here
  // ("" = cleared), laid over what the server holds. Nothing is seeded, so
  // nothing can be lost between the read landing and a seed applying — a
  // sign-in before the read, then Save in that instant, sent the sign-in
  // alone as the whole set (review A22). The `.env` box edits these values;
  // it is not their copy (it trims and cannot hold a line break).
  const [mineEdits, setMineEdits] = useState<Record<string, string>>({});
  const mineServer = mineQ.data?.values ?? {};
  const mineValues = { ...mineServer, ...mineEdits };
  const setMineVars = (env: Record<string, string>) => setMineEdits((prev) => ({ ...prev, ...env }));
  // What the deploy filled in at their last request — shown, not editable, and
  // it wins a name (the server's `unattended_layer` / `private_layer`).
  const auto = mineQ.data?.auto ?? {};

  const [creds, setCreds] = useState<Record<string, string>>({});
  const sharedDirty = text !== toEnvText(envVars) || !sameShape(policy, envPolicy);
  const mineDirty = Object.entries(mineEdits).some(([n, v]) => v !== (mineServer[n] ?? ""));
  const personalDirty = Object.keys(personalChange()).length > 0;
  const dirty =
    sharedDirty || mineDirty || personalDirty || Object.values(creds).some((v) => v.trim() !== "");
  const attemptClose = useDirtyClose(dirty, onClose);

  const tools = toolsQ.data?.tools ?? [];
  const canEdit = onSave !== undefined;
  // Sections are drawn once what decides their grouping and default fold has
  // arrived — otherwise a declared variable first appears under "Other
  // variables", then jumps under its tool, and a section unfolded for a missing
  // value folds again the moment the person's own value loads.
  const toolsSettled = !hasItem || toolsQ.isSuccess || toolsQ.isError;
  const mineSettled = !hasItem || mineQ.isSuccess || mineQ.isError;

  /** After one tab saves: close only if no OTHER tab has anything unsaved;
   * otherwise stay, on the first that does (#779 — a Save is a deliberate exit,
   * and it must not throw away another tab's work without asking). */
  const afterSave = (saved: Tab) => {
    const waiting = { shared: sharedDirty, mine: mineDirty, personal: personalDirty };
    const next = TABS.find((x) => x !== saved && waiting[x]);
    if (next) setTab(next);
    else onClose();
  };
  // Mutations, not bare awaits: a failed write reaches the app's one write-
  // failure notice (MutationCache.onError) instead of failing in silence.
  const saveMine = useMutation({
    mutationFn: async () => {
      // A cleared field is "no value of mine": stored as absence, so the saved
      // set says what the person meant rather than keeping a blank that every
      // reader then has to skip (a blank is not a value, plan N5).
      const kept = Object.fromEntries(Object.entries(mineValues).filter(([, v]) => v !== ""));
      await privateClient.put(slug!, itemId!, kept);
      return kept;
    },
    onSuccess: async (kept) => {
      setMineEdits({});
      queryClient.setQueryData(qk.privateEnv(slug!, itemId!), { values: kept, auto });
      afterSave("mine");
    },
  });
  // Every write to my environment variables: re-read the row (`staleTime: 0`,
  // never the cached copy — round 1, F2), change only the names given, write
  // it whole. One queue with the page's writes (round 2, F2).
  const writePersonal = async (change: Record<string, string>) => {
    const current = (
      await queryClient.fetchQuery({
        queryKey: qk.personalEnv(),
        queryFn: () => personalClient.get(),
        staleTime: 0,
      })
    ).values;
    const next = { ...current };
    // A cleared field is "no value of mine", as on the Private tab.
    for (const [n, v] of Object.entries(change)) {
      if (v === "") delete next[n];
      else next[n] = v;
    }
    return personalClient.put(next);
  };
  // A sign-in on the cross-workspace tab is stored at once: it is the
  // deliberate act, and a token waiting for a Save is one the next run lacks.
  const signIn = useMutation({
    scope: PERSONAL_ENV_WRITES,
    // Its failure is said in the sign-in dialog, which stays open; the
    // app-wide notice would say it twice (review A20 round 2, F1).
    meta: { silentError: true },
    mutationFn: writePersonal,
    onSuccess: (saved, env) => {
      queryClient.setQueryData(qk.personalEnv(), saved);
      // Stored: shown, and no longer an unsaved edit — whatever was typed for
      // those names before.
      setPersonalEdits((prev) => Object.fromEntries(Object.entries(prev).filter(([n]) => !(n in env))));
    },
  });
  const savePersonal = useMutation({
    scope: PERSONAL_ENV_WRITES,
    mutationFn: () => writePersonal(personalChange()),
    onSuccess: (saved) => {
      queryClient.setQueryData(qk.personalEnv(), saved);
      setPersonalEdits({});
      afterSave("personal");
    },
  });
  const logout = useMutation({
    mutationFn: () => privateClient.clear(slug!, itemId!),
    onSuccess: async () => {
      setMineEdits({});
      // Gone now, not when the refetch lands: until then the old row showed
      // and Save wrote it back (review of P19, F1).
      queryClient.setQueryData(qk.privateEnv(slug!, itemId!), { values: {}, auto: {} });
      await queryClient.invalidateQueries({ queryKey: qk.privateEnv(slug!, itemId!) });
    },
  });
  // Clearing cannot be undone (a signed-in token must be signed in for
  // again), so it is marked destructive and asks first, the choice named by
  // its outcome — NN/g "Confirmation Dialogs"; GOV.UK "Warning button" (A22).
  const dialog = useDialog();
  const askToClear = async () => {
    const choice = await dialog.confirm({
      title: t("env.clearTitle"),
      body: t("env.clearBody"),
      actions: [
        { id: "cancel", label: t("env.cancel") },
        { id: "clear", label: t("env.clearConfirm"), variant: "danger" },
      ],
    });
    if (choice === "clear") logout.mutate();
  };
  const saveShared = async () => {
    if (!onSave) return;
    // `false` = the write failed (the app's write-failure notice says so):
    // stay open with the edits rather than close over work that was not stored.
    if ((await onSave(parseEnvText(text), policy)) === false) return;
    // A page's platform strip reads these through its own query.
    if (hasItem) await queryClient.invalidateQueries({ queryKey: qk.envLayers(slug!, itemId!) });
    afterSave("shared");
  };

  // A provider is offered when it produces a name some tool asked for — the
  // name is the ONLY join, so a third-party author never chooses which
  // credential this dialog asks for (#750).
  const declared = new Set(tools.flatMap((x) => (x.env_needs ?? []).map((n) => n.name)));
  const offered = (providersQ.data ?? []).filter((p) => p.produces.some((n) => declared.has(n)));

  // Put the person at the variable the card asked for, once, when its field is
  // drawn. Left to the modal, focus lands on the first tab.
  const focused = useRef(false);
  useEffect(() => {
    if (!target || focused.current) return;
    if (tab !== "mine") return;
    const el = document.querySelector<HTMLElement>(
      `[data-testid="${CSS.escape(`env-mine-${target.name}`)}"]`,
    );
    if (!el) return;
    focused.current = true;
    el.focus();
    el.scrollIntoView?.({ block: "center" });
  });

  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("env.title")}
      data-testid="env-modal"
      width={560}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{t("env.title")}</strong>

      {hasItem && (
        // The app's one tab strip (`Tabs`, the share dialog's too): a filled button is how this app draws its
        // primary ACTION, so a selected tab drawn that way read as "press me"
        // (A22). One component, so the two dialogs' tabs never drift apart.
        <Tabs
          tabs={TABS.map((id) => ({ id, label: t(`env.tab.${id}`), count: 0 }))}
          value={tab}
          onChange={(id) => setTab(id as Tab)}
          idPrefix="env"
          label={t("env.title")}
        />
      )}

      {hasItem && (
        <input
          type="search"
          className="input input--block"
          data-testid="env-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("env.search")}
          aria-label={t("env.search")}
        />
      )}

      <div
        className="scrollable"
        // What the strip's `aria-controls` points at (`Tabs`: `{idPrefix}-panel-{tab}`).
        role={hasItem ? "tabpanel" : undefined}
        id={hasItem ? `env-panel-${tab}` : undefined}
        aria-labelledby={hasItem ? `env-tab-${tab}` : undefined}
        style={{ overflowY: "auto", minHeight: 0, display: "grid", gap: 10 }}
      >
        {tab === "shared" ? (
          <SharedTab
            settled={toolsSettled}
            tools={tools}
            query={query}
            shared={shared}
            policy={policy}
            canEdit={canEdit}
            onVar={setVar}
            onPolicy={choosePolicy}
            text={text}
            setText={setText}
            login={
              <Logins
                offered={offered}
                disabled={!canEdit}
                creds={creds}
                setCreds={setCreds}
                exchange={(id, values) => client.resolveEnvProvider(slug!, itemId!, id, values)}
                onFilled={(env) =>
                  setText((prev) =>
                    Object.entries(env).reduce((acc, [n, v]) => setEnvValue(acc, n, v), prev),
                  )
                }
              />
            }
          />
        ) : tab === "personal" ? (
          <PersonalTab
            settled={toolsSettled && (personalQ.isSuccess || personalQ.isError)}
            tools={tools}
            query={query}
            policy={policy}
            values={personalValues}
            failed={personalQ.isError}
            saving={savePersonal.isPending}
            onEdit={(name, value) => setPersonalEdits((prev) => ({ ...prev, [name]: value }))}
            // The box replaces the whole layer: a name taken out of it is removed.
            onReplace={(next) =>
              setPersonalEdits(
                Object.fromEntries([
                  ...Object.keys({ ...personal, ...personalEdits }).map((n) => [n, ""] as const),
                  ...Object.entries(next),
                ]),
              )
            }
            ready={personalQ.isSuccess}
            login={
              <Logins
                offered={offered}
                creds={creds}
                setCreds={setCreds}
                exchange={(id, values) => client.resolveEnvProvider(slug!, itemId!, id, values)}
                // Awaited: a value that could not be stored keeps the sign-in
                // open and says so (review A20, D5).
                onFilled={(env) => signIn.mutateAsync(env)}
              />
            }
          />
        ) : (
          <MineTab
            settled={toolsSettled && mineSettled}
            tools={tools}
            query={query}
            shared={shared}
            policy={policy}
            mine={mineValues}
            auto={auto}
            personal={personal}
            failed={mineQ.isError}
            setMine={(name, value) => setMineVars({ [name]: value })}
            // The box replaces the whole layer: a name taken out of it is cleared.
            onReplace={(next) =>
              setMineEdits(
                Object.fromEntries([
                  ...Object.keys(mineValues).map((n) => [n, ""] as const),
                  ...Object.entries(next),
                ]),
              )
            }
            // Not before the read: typing here would become the whole set.
            ready={mineQ.isSuccess}
            focus={target?.name ?? null}
            login={
              <Logins
                offered={offered}
                creds={creds}
                setCreds={setCreds}
                exchange={(id, values) => client.resolveEnvProvider(slug!, itemId!, id, values)}
                // This tab is my values for THIS item, so that is where its
                // sign-in lands — saved with this tab (A20).
                onFilled={(env) => setMineVars(env)}
              />
            }
          />
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8 }}>
        {tab === "shared" ? (
          <>
            {canEdit && (
              <span data-testid="env-affects" style={{ ...MUTED, marginRight: "auto" }}>
                {t("env.affectsEveryone")}
              </span>
            )}
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="env-cancel"
              style={canEdit ? undefined : { marginLeft: "auto" }}
              onClick={attemptClose}
            >
              {t("env.cancel")}
            </button>
            {canEdit && (
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-testid="env-save"
                onClick={() => void saveShared()}
              >
                {t("env.save")}
              </button>
            )}
          </>
        ) : tab === "personal" ? (
          <>
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="env-cancel"
              style={{ marginLeft: "auto" }}
              onClick={attemptClose}
            >
              {t("env.cancel")}
            </button>
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-testid="env-personal-save"
              // Not over a failed read: the row would be written from nothing.
              disabled={!personalQ.isSuccess || savePersonal.isPending}
              // Nothing typed: nothing to write — a whole-row PUT for no change
              // could only race another tab (review A20, D6).
              onClick={() => (personalDirty ? savePersonal.mutate() : afterSave("personal"))}
            >
              {t("env.save")}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn"
              data-variant="danger"
              data-size="sm"
              data-testid="env-mine-logout"
              style={{ marginRight: "auto" }}
              // Nothing of mine stored here: nothing to clear (review A22). Only
              // when that is KNOWN: clearing is deliberately allowed without
              // read access, so someone who lost it can still take their
              // credential back out (`private_env.py`, the DELETE route).
              disabled={
                mineQ.isSuccess &&
                Object.keys(mineQ.data?.values ?? {}).length === 0 &&
                Object.keys(auto).length === 0
              }
              onClick={() => void askToClear()}
            >
              {t("env.logout")}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="env-cancel"
              onClick={attemptClose}
            >
              {t("env.cancel")}
            </button>
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-testid="env-mine-save"
              // Not until the person's values have loaded: Save replaces the
              // whole set, and saving over a failed read erased it (F5).
              disabled={!mineQ.isSuccess || saveMine.isPending}
              onClick={() => saveMine.mutate()}
            >
              {t("env.save")}
            </button>
          </>
        )}
      </div>
    </ModalShell>
  );
}

// ── sections ──────────────────────────────────────────────────────────────

/** Sections matching the search: by tool name / publisher, or by a variable
 * one of them asks for. */
function matching(sections: ToolSection[], query: string): ToolSection[] {
  const q = query.trim().toLowerCase();
  if (!q) return sections;
  return sections.filter(
    (s) =>
      `${s.label} ${s.author ?? ""}`.toLowerCase().includes(q) ||
      s.fields.some((f) => f.name.toLowerCase().includes(q)),
  );
}

function Section({
  section,
  open,
  onToggle,
  children,
  claimsStatus = true,
}: {
  section: ToolSection;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  /** False for "Other variables": no tool declared them, so there is nothing
   * to be ready FOR, and a "✓ Ready" over an unset row was a false sentence. */
  claimsStatus?: boolean;
}) {
  const t = useT();
  const look = STATUS_LOOK[section.status];
  const words =
    section.status === "missingRequired"
      ? t("env.status.missingRequired", { count: String(section.missingRequired) })
      : section.status === "missingOptional"
        ? t("env.status.missingOptional", { count: String(section.missingOptional) })
        : t("env.status.ready");
  const bodyId = useId();
  return (
    <section style={{ borderTop: "1px solid var(--paper-3)", paddingTop: 6 }}>
      <button
        type="button"
        data-testid={`env-section-head-${section.key}`}
        data-status={claimsStatus ? section.status : undefined}
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          width: "100%",
          padding: "4px 0",
          background: "transparent",
          border: 0,
          textAlign: "left",
          cursor: "pointer",
        }}
      >
        <span aria-hidden style={{ ...MUTED, width: 10 }}>
          {open ? "▾" : "▸"}
        </span>
        {claimsStatus && (
          <span aria-hidden style={{ color: look.color }}>
            {look.mark}
          </span>
        )}
        <span style={{ fontWeight: 500, fontSize: pxToRem(13) }}>{section.label}</span>
        {(section.author || section.version) && (
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
            {[section.author, section.version].filter(Boolean).join(" · ")}
          </span>
        )}
        {claimsStatus && (
          <span style={{ marginLeft: "auto", fontSize: pxToRem(11), color: look.color }}>
            {words}
          </span>
        )}
      </button>
      {open && (
        <div id={bodyId} style={{ display: "grid", gap: 10, padding: "4px 0 8px 18px" }}>
          {children}
        </div>
      )}
    </section>
  );
}

function Sections({
  sections,
  query,
  other,
  row,
  otherExtra,
  otherOpen = false,
  focus = null,
}: {
  sections: ToolSection[];
  query: string;
  /** Names no tool declared, drawn as a last section. */
  other: string[];
  row: (field: EnvField, section: ToolSection) => ReactNode;
  otherExtra?: ReactNode;
  /** Unfold "Other variables" from the start — it holds what the person came
   * to fill. */
  otherOpen?: boolean;
  /** The variable a card asked for: its section starts unfolded. */
  focus?: string | null;
}) {
  const t = useT();
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  // The default fold is decided ONCE, when the list is first drawn (it is only
  // drawn once its data has arrived): unfolded where a required value is
  // missing. Recomputing it would fold a section the moment the person filled
  // the value they came for — the fold would move under their hands.
  const [initial] = useState<Record<string, boolean>>(() =>
    ({
      ...Object.fromEntries(
        sections.map((s) => [
          s.key,
          s.status === "missingRequired" || s.fields.some((f) => f.name === focus),
        ]),
      ),
      __other: otherOpen || (focus !== null && other.includes(focus)),
    }),
  );
  const q = query.trim().toLowerCase();
  const otherSection: ToolSection = {
    key: "__other",
    label: t("env.otherVars"),
    author: null,
    version: null,
    status: "ready",
    missingRequired: 0,
    missingOptional: 0,
    fields: other
      .filter((n) => !q || n.toLowerCase().includes(q))
      .map((name) => ({ name, description: "", required: null, wantedBy: [], filled: true })),
  };
  const shown = matching(sections, query);
  const all = [...shown, ...(otherSection.fields.length > 0 || otherExtra ? [otherSection] : [])];
  if (all.length === 0) {
    return <p style={MUTED}>{t("env.noMatches")}</p>;
  }
  return (
    <>
      {all.map((s) => {
        // A search unfolds what it found; otherwise the first-drawn default
        // (the "other" section too when there are no tools at all). The
        // person's own clicks win over both.
        const byDefault = q !== "" || (initial[s.key] ?? false) || sections.length === 0;
        const open = overrides[s.key] ?? byDefault;
        return (
          <Section
            key={s.key}
            section={s}
            open={open}
            onToggle={() => setOverrides((prev) => ({ ...prev, [s.key]: !open }))}
            claimsStatus={s.key !== "__other"}
          >
            {s.fields.map((f) => row(f, s))}
            {s.key === "__other" && otherExtra}
          </Section>
        );
      })}
    </>
  );
}

// ── Shared ──────────────────────────────────────────────────────────────

function SharedTab({
  settled,
  tools,
  query,
  shared,
  policy,
  canEdit,
  onVar,
  onPolicy,
  text,
  setText,
  login,
}: {
  settled: boolean;
  tools: Parameters<typeof deriveEnvNeeds>[0];
  query: string;
  shared: Record<string, string>;
  policy: Record<string, string>;
  canEdit: boolean;
  onVar: (name: string, value: string) => void;
  onPolicy: (name: string, p: EnvPolicy) => void;
  text: string;
  setText: (next: string) => void;
  login: ReactNode;
}) {
  const t = useT();
  const view = deriveEnvNeeds(tools, shared);
  const declared = new Set(view.sections.flatMap((s) => s.fields.map((f) => f.name)));
  const other = [...new Set([...Object.keys(shared), ...Object.keys(policy)])].filter(
    (n) => !declared.has(n),
  );
  const [newName, setNewName] = useState("");

  return (
    <>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("env.sharedDesc")}
      </p>
      {!canEdit && (
        <p data-testid="env-readonly" style={{ margin: 0, fontSize: pxToRem(12) }}>
          {t("env.readonly")}
        </p>
      )}
      {view.sections.length > 0 && (
        <p data-testid="env-missing" style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
          {view.missingRequired.length > 0
            ? t("env.stillMissing", { names: view.missingRequired.join(", ") })
            : t("env.nothingMissing")}
        </p>
      )}
      {!settled ? (
        <p data-testid="env-loading" style={MUTED}>
          …
        </p>
      ) : (
      <Sections
        sections={view.sections}
        query={query}
        other={other}
        row={(field, section) => (
          <SharedRow
            key={`${section.key}:${field.name}`}
            field={field}
            section={section}
            value={shared[field.name] ?? ""}
            policy={policyOf(field.name, policy)}
            // A blank is not a value (N5): no "the shared value is unused" note.
            hasShared={!isBlank(shared[field.name])}
            canEdit={canEdit}
            onVar={onVar}
            onPolicy={onPolicy}
          />
        )}
        otherExtra={
          canEdit ? (
            <div style={{ display: "flex", gap: 6 }}>
              <input
                className="input"
                data-testid="env-add-private-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={t("env.addPrivateName")}
                aria-label={t("env.addPrivateName")}
                spellCheck={false}
                style={MONO}
              />
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                data-testid="env-add-private"
                disabled={!newName.trim() || unstorable({ [newName.trim()]: "x" }).length > 0}
                onClick={() => {
                  onPolicy(newName.trim(), "private_only");
                  setNewName("");
                }}
              >
                {t("env.addPrivate")}
              </button>
            </div>
          ) : null
        }
      />
      )}
      {login}
      <EnvTextBox prefix="env" text={text} setText={setText} readOnly={!canEdit} />
    </>
  );
}

// ── the `.env` box ────────────────────────────────────────────────────────

/** Each tab's values as `.env` text — the tab's ONE copy, which its fields
 * edit in place (`setEnvValue`, so a keystroke in a field never costs the
 * comments written in the box). Folded: it is for what people do with these,
 * paste a block from elsewhere. One component for every tab (A22). */
function EnvTextBox({
  prefix,
  text,
  setText,
  readOnly,
}: {
  prefix: string;
  text: string;
  /** `imported`: the text came from a file, not from typing in the box. */
  setText: (next: string, imported?: boolean) => void;
  readOnly: boolean;
}) {
  const t = useT();
  const fileRef = useRef<HTMLInputElement>(null);
  const importFile = async (file: File) => {
    // MERGES into what is in the box — the thing the person is looking at.
    setText(toEnvText(mergeEnv(parseEnvText(text), parseEnvText(await file.text()))), true);
  };
  const exportFile = () => {
    // What is in the box, unsaved edits included; to the browser, never into
    // the workspace (a file there is one the agent can read).
    const url = URL.createObjectURL(new Blob([toEnvText(parseEnvText(text))], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = ".env";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };
  return (
    <details data-testid={`${prefix}-text-details`}>
      <summary style={{ cursor: "pointer", fontSize: pxToRem(12) }}>{t("env.editAsText")}</summary>
      <textarea
        data-testid={`${prefix}-text`}
        aria-label={t("env.editAsText")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        readOnly={readOnly}
        placeholder={"FOO=BAR\nBAZ=HOO"}
        spellCheck={false}
        rows={8}
        className="input input--block"
        style={{
          ...MONO,
          marginTop: 6,
          lineHeight: 1.6,
          whiteSpace: "pre",
          overflowWrap: "normal",
          overflowX: "auto",
        }}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <input
          ref={fileRef}
          type="file"
          accept=".env,text/plain"
          data-testid={`${prefix}-import`}
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void importFile(f);
            e.target.value = ""; // so re-picking the same file fires again
          }}
        />
        {!readOnly && (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid={`${prefix}-import-button`}
            onClick={() => fileRef.current?.click()}
          >
            {t("env.import")}
          </button>
        )}
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid={`${prefix}-export`}
          onClick={exportFile}
        >
          {t("env.export")}
        </button>
      </div>
    </details>
  );
}

/** The private tabs' `.env` box: an editor OF the tab's values, not their
 * copy. It shows them as text; what is typed in it replaces the tab's values
 * with what parses. While it is being typed in, the typed text stays as typed;
 * otherwise it follows the fields. Comments are not kept (the Shared box,
 * which IS its layer's copy, keeps them). */
function DerivedEnvBox({
  prefix,
  values,
  onReplace,
  readOnly,
}: {
  prefix: string;
  values: Record<string, string>;
  onReplace: (next: Record<string, string>) => void;
  readOnly: boolean;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  // A cleared field is "no value", not a `NAME=` line to show or export.
  const shown = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
  return (
    <div onBlur={() => setDraft(null)}>
      <EnvTextBox
        prefix={prefix}
        text={draft ?? toEnvText(shown)}
        setText={(next, imported) => {
          // Only typing keeps a draft; after an import the box follows the
          // fields again (review of P19, F3).
          setDraft(imported ? null : next);
          // A line nobody touched keeps its stored value: the text format
          // trims and cannot hold a line break, so a value it shows can parse
          // back different (review of P19, F4).
          const parsed = parseEnvText(next);
          onReplace(
            Object.fromEntries(
              Object.entries(parsed).map(([n, v]) => {
                const was = shown[n];
                return [n, was !== undefined && parseEnvText(toEnvText({ [n]: was }))[n] === v ? was : v];
              }),
            ),
          );
        }}
        readOnly={readOnly}
      />
    </div>
  );
}

function SharedRow({
  field,
  section,
  value,
  policy,
  hasShared,
  canEdit,
  onVar,
  onPolicy,
}: {
  field: EnvField;
  section: ToolSection;
  value: string;
  policy: EnvPolicy;
  hasShared: boolean;
  canEdit: boolean;
  onVar: (name: string, value: string) => void;
  onPolicy: (name: string, p: EnvPolicy) => void;
}) {
  const t = useT();
  const others = field.wantedBy.filter((w) => w !== section.label);
  return (
    <div data-testid={`env-row-${field.name}`} style={{ display: "grid", gap: 4 }}>
      <label style={{ display: "grid", gap: 2 }}>
        <span style={MONO}>{field.name}</span>
        {field.description && <span style={MUTED}>{field.description}</span>}
        {others.length > 0 && (
          <span data-testid={`env-shared-${field.name}`} style={MUTED}>
            {t("env.alsoUsedBy", { tools: others.join(", ") })}
          </span>
        )}
        <input
          data-testid={`env-field-${field.name}`}
          value={value}
          onChange={(e) => onVar(field.name, e.target.value)}
          readOnly={!canEdit}
          spellCheck={false}
          // Never `true`: an empty required field is NOT YET FILLED, not wrong
          // — the declaration is a hint and Save always works (#750).
          aria-invalid="false"
          className="input input--block"
          style={MONO}
        />
      </label>
      {/* Radios, not a dropdown: three options fit on screen, and a closed
          dropdown hides what the choices even are (NN/g). Named PER SECTION so a
          variable shown under two tools gets two independent native groups
          that both reflect the one stored policy. */}
      <div
        role="radiogroup"
        aria-label={t("env.policyLabel", { name: field.name })}
        style={{ display: "flex", flexWrap: "wrap", gap: 10, fontSize: pxToRem(12) }}
      >
        {POLICIES.map((p) => (
          <label key={p} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <input
              type="radio"
              name={`env-policy-${section.key}-${field.name}`}
              data-testid={`env-policy-${field.name}-${p}`}
              checked={policy === p}
              disabled={!canEdit}
              onChange={() => onPolicy(field.name, p)}
            />
            {t(`env.policy.${p}`)}
          </label>
        ))}
      </div>
      {policy === "private_only" && hasShared && (
        <span data-testid={`env-shared-unused-${field.name}`} style={MUTED}>
          {t("env.sharedUnused")}
        </span>
      )}
    </div>
  );
}

// ── Private ───────────────────────────────────────────────────────────────

function MineTab({
  settled,
  tools,
  query,
  shared,
  policy,
  mine,
  auto,
  personal,
  failed,
  setMine,
  onReplace,
  ready,
  login,
  focus = null,
}: {
  settled: boolean;
  tools: Parameters<typeof deriveEnvNeeds>[0];
  query: string;
  shared: Record<string, string>;
  policy: Record<string, string>;
  /** The variable a card asked for — drawn even if nothing declared it (D5). */
  focus?: string | null;
  mine: Record<string, string>;
  auto: Record<string, string>;
  personal: Record<string, string>;
  failed: boolean;
  setMine: (name: string, value: string) => void;
  onReplace: (next: Record<string, string>) => void;
  ready: boolean;
  login: ReactNode;
}) {
  const t = useT();
  // What a tool would get for each name, so a section's status is about THIS
  // person — a tool "ready" for everyone can still be missing their own value.
  // The person's private layer as a tool gets it: what the deploy filled in
  // wins a name over what they typed (the server's composition).
  const own = ownLayer(mine, auto);
  const names = new Set([
    ...tools.flatMap((x) => (x.env_needs ?? []).map((n) => n.name)),
    ...Object.keys(shared),
    ...Object.keys(own),
  ]);
  const effective: Record<string, string> = {};
  for (const n of names) {
    const layer = layerInUse(n, shared, own, policy, personal);
    if (layer !== "none")
      effective[n] = (layer === "private" ? own : layer === "personal" ? personal : shared)[n];
  }
  const view = deriveEnvNeeds(tools, effective);
  const declared = new Set(view.sections.flatMap((s) => s.fields.map((f) => f.name)));
  // Undeclared names worth showing here: ones the person holds, and ones the
  // item asks each person to fill in.
  const other = [
    ...new Set([
      ...Object.keys(own),
      ...Object.keys(policy).filter((n) => policyOf(n, policy) !== "shared_first"),
      ...(focus ? [focus] : []),
    ]),
  ].filter((n) => !declared.has(n));

  return (
    <>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("env.mineDesc")}
      </p>
      {failed && (
        <p role="alert" style={{ margin: 0, fontSize: pxToRem(12), color: "var(--err)" }}>
          {t("env.mineFailed")}
        </p>
      )}
      {!settled ? (
        <p data-testid="env-loading" style={MUTED}>
          …
        </p>
      ) : (
      <Sections
        focus={focus}
        sections={view.sections}
        query={query}
        other={other}
        // What only THEY can fill and have not: unfold it, or a person who
        // opened this from a page finds it folded below every tool.
        otherOpen={other.some(
          (n) => policyOf(n, policy) === "private_only" && !own[n] && !personal[n],
        )}
        row={(field, section) => (
          <MineRow
            key={`${section.key}:${field.name}`}
            name={field.name}
            description={field.description}
            shared={shared}
            policy={policy}
            mine={mine}
            auto={auto}
            personal={personal}
            setMine={setMine}
          />
        )}
      />
      )}
      {login}
      <DerivedEnvBox prefix="env-mine" values={mine} onReplace={onReplace} readOnly={!ready} />
    </>
  );
}

function MineRow({
  name,
  description,
  shared,
  policy,
  mine,
  auto,
  personal,
  setMine,
}: {
  name: string;
  description: string;
  shared: Record<string, string>;
  policy: Record<string, string>;
  mine: Record<string, string>;
  auto: Record<string, string>;
  personal: Record<string, string>;
  setMine: (name: string, value: string) => void;
}) {
  const t = useT();
  const [revealed, setRevealed] = useState(false);
  const p = policyOf(name, policy);
  const layer = layerInUse(name, shared, ownLayer(mine, auto), policy, personal);
  // Filled in by the deploy at the person's last request: it wins over
  // anything typed, and their next request rewrites it — a box here would
  // edit nothing.
  const automatic = Object.hasOwn(auto, name);
  // `shared_first` with a shared value set: nothing the person types could be
  // used, so no box is offered — a field that silently does nothing is worse.
  // Read off `layerInUse`, the one rule of what a tool gets: a blank shared
  // value is not a value (N5), so it pins nothing and theirs is used.
  const pinned = p === "shared_first" && layer === "shared";
  const inUse = layer === "private" ? "mine" : layer;
  return (
    <div data-testid={`env-mine-row-${name}`} data-in-use={inUse} style={{ display: "grid", gap: 3 }}>
      <span style={MONO}>{name}</span>
      {description && <span style={MUTED}>{description}</span>}
      {pinned ? (
        <span data-testid={`env-pinned-${name}`} style={MUTED}>
          {t("env.pinned")}
        </span>
      ) : automatic ? (
        <span data-testid={`env-auto-${name}`} style={MUTED}>
          {t("env.hint.auto")}
        </span>
      ) : (
        <div style={{ display: "flex", gap: 6 }}>
          <input
            data-testid={`env-mine-${name}`}
            // Masked: these are the person's own credentials and only they can
            // read them — but a screen can be seen over a shoulder.
            type={revealed ? "text" : "password"}
            value={mine[name] ?? ""}
            onChange={(e) => setMine(name, e.target.value)}
            autoComplete="off"
            spellCheck={false}
            // `.input` (fills what is left), not `.input--block` (width 100%,
            // never shrinks): beside the reveal button that pushed the button out of
            // the panel and gave it a sideways scroll — on master too.
            className="input"
            style={MONO}
          />
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid={`env-reveal-${name}`}
            aria-pressed={revealed}
            onClick={() => setRevealed((r) => !r)}
          >
            {t(revealed ? "env.hide" : "env.reveal")}
          </button>
        </div>
      )}
      {/* Each phrase whole: at phone width the two used to break mid-name side
          by side ("Private(跨 / workspace)"); now the second wraps under. */}
      <span style={{ ...MUTED, display: "flex", flexWrap: "wrap", columnGap: 8 }}>
        {/* What typing here would take over from — named by its tab, so the
            words are the ones on the tabs above (A22). Nothing when this tab
            already holds the value, or when nothing is in use to take over. */}
        {!pinned && !automatic && p !== "shared_first" && (layer === "shared" || layer === "personal") && (
          <span style={{ whiteSpace: "nowrap" }}>{t("env.override", { tab: t(`env.tab.${TAB_OF[layer]}`) })}</span>
        )}
        <span style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
          {layer === "none" ? (
            t("env.inUse.none")
          ) : (
            <>
              {inUse === "mine" || inUse === "personal" ? "✓ " : ""}
              {t("env.inUse", { tab: t(`env.tab.${TAB_OF[layer]}`) })}
            </>
          )}
        </span>
      </span>
    </div>
  );
}

// ── Private(跨workspace) ────────────────────────────────────────────────

function PersonalTab({
  settled,
  tools,
  query,
  policy,
  values,
  failed,
  saving,
  onEdit,
  onReplace,
  ready,
  login,
}: {
  settled: boolean;
  tools: Parameters<typeof deriveEnvNeeds>[0];
  query: string;
  policy: Record<string, string>;
  /** My values for every item, as the box holds them now. */
  values: Record<string, string>;
  failed: boolean;
  /** A save is on its way: what is typed now would be dropped as it lands. */
  saving: boolean;
  onEdit: (name: string, value: string) => void;
  onReplace: (next: Record<string, string>) => void;
  ready: boolean;
  login: ReactNode;
}) {
  const t = useT();
  // The tools' sections, searched by the SAME rule as the other tabs
  // (`matching`: tool name, publisher or variable — review A20, D1).
  const sections = deriveEnvNeeds(tools, {}).sections;
  const needed = new Map<string, string>();
  for (const f of sections.flatMap((x) => x.fields)) if (!needed.has(f.name)) needed.set(f.name, f.description);
  // Names the item asks each person for without a tool declaring them.
  const asked = Object.keys(policy).filter((n) => policyOf(n, policy) !== "shared_first" && !needed.has(n));
  for (const n of asked) needed.set(n, "");
  const q = query.trim().toLowerCase();
  const found = new Set([
    ...matching(sections, query).flatMap((x) => x.fields.map((f) => f.name)),
    ...asked.filter((n) => !q || n.toLowerCase().includes(q)),
  ]);
  const names = [...needed.keys()].filter((n) => found.has(n));
  return (
    <>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {/* The way to the page that holds them is the sentence's own link, so
            it reads as one thing (A22) — placed by the sentence itself, so
            each language brings its own brackets. Styled as the docs' inline
            links (`.md-body a`): the base `a` rule inherits the text colour. */}
        {t("env.personalDesc").split("{link}").map((part, i) =>
          i === 0 ? (
            part
          ) : (
            <span key={i}>
              <a
                href="/my-env"
                data-testid="env-personal-page"
                style={{ color: "var(--accent-h)", textDecoration: "underline", whiteSpace: "nowrap" }}
              >
                {t("env.personalPage")}
              </a>
              {part}
            </span>
          ),
        )}
      </p>
      {failed && (
        <p role="alert" style={{ margin: 0, fontSize: pxToRem(12), color: "var(--err)" }}>
          {t("myEnv.loadFailed")}
        </p>
      )}
      {!settled ? (
        <p style={MUTED}>…</p>
      ) : needed.size === 0 ? (
        // Nothing to fill is not a search that matched nothing (review A20, D2).
        <p data-testid="env-personal-none" style={MUTED}>
          {t("env.personalNone")}
        </p>
      ) : names.length === 0 ? (
        <p style={MUTED}>{t("env.noMatches")}</p>
      ) : (
        names.map((name) => (
          <PersonalRow
            key={name}
            name={name}
            description={needed.get(name) ?? ""}
            value={values[name] ?? ""}
            disabled={saving}
            onEdit={onEdit}
          />
        ))
      )}
      {login}
      <DerivedEnvBox prefix="env-personal" values={values} onReplace={onReplace} readOnly={saving || !ready} />
    </>
  );
}

function PersonalRow({
  name,
  description,
  value,
  disabled,
  onEdit,
}: {
  name: string;
  description: string;
  value: string;
  disabled: boolean;
  onEdit: (name: string, value: string) => void;
}) {
  const t = useT();
  const [revealed, setRevealed] = useState(false);
  return (
    <div data-testid={`env-personal-row-${name}`} style={{ display: "grid", gap: 3 }}>
      <span style={MONO}>{name}</span>
      {description && <span style={MUTED}>{description}</span>}
      <div style={{ display: "flex", gap: 6 }}>
        <input
          data-testid={`env-personal-${name}`}
          type={revealed ? "text" : "password"}
          value={value}
          disabled={disabled}
          onChange={(e) => onEdit(name, e.target.value)}
          autoComplete="off"
          spellCheck={false}
          // `.input` (fills what is left), not `.input--block` (width 100%,
          // never shrinks): beside the reveal button that pushed the button out of
          // the panel and gave it a sideways scroll — on master too.
          className="input"
          style={MONO}
        />
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          aria-pressed={revealed}
          onClick={() => setRevealed((r) => !r)}
        >
          {t(revealed ? "env.hide" : "env.reveal")}
        </button>
      </div>
    </div>
  );
}

// ── logins (`IEnvProvider`, #750) ─────────────────────────────────────────

/** The deploy's "log in, get the variables" buttons. The exchange result goes
 * to the caller's `onFilled`, which puts it in that tab's own layer: Shared and
 * Private fill their form (saved with Save); the cross-workspace tab and the
 * My environment variables page store it at once (`docs/plan-personal-env.md`).
 * The credential typed here reaches the deploy's implementation and stops. */
export function Logins({
  offered,
  initialDialog = null,
  onCancel,
  formOnly = false,
  disabled = false,
  creds,
  setCreds,
  exchange,
  onFilled,
}: {
  offered: EnvProvider[];
  /** Open on this login's form — the card's login page (`EnvLoginModal`). */
  initialDialog?: string | null;
  /** Where the form's Cancel goes when it is the whole page (`EnvLoginModal`):
   * there, collapsing the form would leave a page with nothing to do. */
  onCancel?: () => void;
  /** Draw only the open form, not the list of logins to pick from — the
   * card's login page, where the one login is already chosen. */
  formOnly?: boolean;
  disabled?: boolean;
  creds: Record<string, string>;
  setCreds: (next: Record<string, string>) => void;
  exchange: (providerId: string, values: Record<string, string>) => Promise<Record<string, string>>;
  /** May return a promise: a rejection keeps the dialog open with a reason. */
  onFilled: (env: Record<string, string>) => void | Promise<unknown>;
}) {
  const t = useT();
  const [dialog, setDialog] = useState<string | null>(initialDialog);
  const [credError, setCredError] = useState<string | null>(null);
  const [exchanging, setExchanging] = useState(false);
  const openProvider = offered.find((p) => p.id === dialog);

  const runExchange = async () => {
    if (!openProvider) return;
    setExchanging(true);
    setCredError(null);
    try {
      const env = await exchange(openProvider.id, creds);
      // Refused WHOLE and by name when a pair cannot survive the text format:
      // a truncated certificate is not recoverable, being told is.
      const cannotStore = unstorable(env);
      if (cannotStore.length > 0) {
        setCredError(t("env.providerValueTooComplex", { names: cannotStore.join(", ") }));
        return;
      }
      try {
        await onFilled(env);
      } catch {
        // Signed in, but not stored: closing would lose the token in silence.
        setCredError(t("env.signInNotSaved"));
        return;
      }
      setDialog(null);
      setCreds({});
    } catch (err) {
      // The implementation's own sentence when it sent one — never the HTTP
      // envelope, which is internals in front of someone trying to log in.
      const why = (err as { detail?: { why?: unknown } })?.detail?.why;
      setCredError(typeof why === "string" && why ? why : t("env.providerFailed"));
    } finally {
      setExchanging(false);
    }
  };

  if (offered.length === 0) return null;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {!formOnly &&
        offered.map((provider) => (
        <div key={provider.id}>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid={`env-provider-${provider.id}`}
            disabled={disabled}
            onClick={() => {
              setDialog(provider.id);
              setCreds({});
              setCredError(null);
            }}
          >
            {provider.label}
          </button>
          <span style={{ marginLeft: 8, ...MUTED }}>
            {t("env.providerFills", { names: provider.produces.join(", ") })}
          </span>
        </div>
      ))}
      {openProvider && (
        <div data-testid="env-cred-dialog" style={{ display: "grid", gap: 6 }}>
          {openProvider.inputs.map((field) => (
            <label key={field.name} style={{ display: "block", fontSize: pxToRem(12) }}>
              {field.label}
              <input
                data-testid={`env-cred-${field.name}`}
                type={field.secret ? "password" : "text"}
                value={creds[field.name] ?? ""}
                onChange={(e) => setCreds({ ...creds, [field.name]: e.target.value })}
                className="input input--block"
              />
            </label>
          ))}
          {credError && (
            <p data-testid="env-cred-error" style={{ margin: 0, fontSize: pxToRem(11), color: "var(--err)" }}>
              {credError}
            </p>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              data-size="sm"
              data-testid="env-cred-submit"
              disabled={exchanging}
              onClick={() => void runExchange()}
            >
              {t("env.providerRun")}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="env-cred-cancel"
              onClick={() => {
                if (onCancel) {
                  onCancel();
                  return;
                }
                setDialog(null);
                setCreds({});
                setCredError(null);
              }}
            >
              {t("env.cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
