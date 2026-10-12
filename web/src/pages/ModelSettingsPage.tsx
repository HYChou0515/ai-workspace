/**
 * Model settings (`docs/plan-model-settings.md` P8): a superuser edits what the
 * platform's models are — presets, each App's app.json and profiles, the
 * platform's own model choices — without a redeploy.
 *
 * The draft is a JSON Merge Patch over the deploy defaults, edited as text (M9):
 * it is checked as you type (the same checks Apply runs), saved as a draft that
 * changes nothing running, and applied with a confirmation that lists every
 * value the Apply changes (M4). Each Apply is a version; any earlier one can be
 * applied again (M5). Keys are write-only and set apart from the text, so they
 * never sit in a diff or the history (M6).
 *
 * Layout (M15): three tabs for three jobs, one task each — NN/g "Tabs, Used
 * Right" (tabs for peer sections of one object, never for sequential steps) and
 * Material 3 "Tabs" (primary tabs, text labels). The destructive-or-global
 * actions (Apply, roll back, clear a key) confirm and name their outcome —
 * NN/g "Confirmation Dialogs Can Prevent User Errors".
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  modelSettingsApi,
  problemsOf,
  type Applied,
  type ModelSettingsClient,
  type SettingsDocument,
} from "../api/modelSettings";
import { qk } from "../api/queryKeys";
import { useDialog } from "../components/Dialog";
import { MonacoEditor } from "../components/MonacoEditor";
import { Tabs } from "../components/ShareTabs";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useIsSuperuserState } from "../hooks/useIsSuperuser";
import { useT } from "../lib/i18n";
import { blockMaskedCopy } from "../lib/maskedSecret";
import { pxToRem } from "../lib/pxToRem";
import { diffPatches, flattenPatch, parsePatch, withoutPath, type Change, type Patch } from "../lib/settingsPatch";

const MONO = { fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: pxToRem(12) };
const MUTED = { fontSize: pxToRem(12), color: "var(--text-paper-d)" } as const;
/** The "in force" tag on a history row — the tokens the skill hub's badge uses. */
const BADGE = {
  display: "inline-block",
  padding: "1px var(--space-8)",
  borderRadius: "var(--radius-btn)",
  fontSize: "var(--text-small)",
  background: "var(--paper-2)",
  color: "var(--text-paper-d)",
  whiteSpace: "nowrap",
} as const;
/** How long typing pauses before the draft is checked. */
const CHECK_AFTER_MS = 400;

const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const short = (version: string) => version.slice(0, 8);
const shown = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

function valueAt(document: SettingsDocument, path: string): { found: boolean; value?: unknown } {
  let node: unknown = document;
  for (const part of path.split(".")) {
    if (typeof node !== "object" || node === null || Array.isArray(node) || !(part in node))
      return { found: false };
    node = (node as Record<string, unknown>)[part];
  }
  return { found: true, value: node };
}

type Client = Pick<
  ModelSettingsClient,
  "get" | "saveDraft" | "check" | "apply" | "history" | "rollback" | "setKey" | "clearKey" | "testConnection"
>;

export function ModelSettingsPage({ client = modelSettingsApi }: { client?: Client }) {
  const t = useT();
  const { isSuperuser, ready } = useIsSuperuserState();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("modelSettings.title") }]);
  const [tab, setTab] = useState("edit");
  const page = useQuery({ queryKey: qk.modelSettings, queryFn: () => client.get(), enabled: isSuperuser });

  if (!ready) return <div className="page">…</div>;
  if (!isSuperuser)
    return (
      <div className="page">
        <h1>{t("modelSettings.title")}</h1>
        <p data-testid="model-settings-not-allowed" style={MUTED}>
          {t("modelSettings.notAllowed")}
        </p>
      </div>
    );

  return (
    <div className="page" style={{ display: "grid", gap: 12 }}>
      <h1>{t("modelSettings.title")}</h1>
      <p style={MUTED}>{t("modelSettings.desc")}</p>
      {page.isPending ? (
        <p style={MUTED}>…</p>
      ) : page.isError ? (
        <p role="alert" style={MUTED}>
          {t("modelSettings.loadFailed")}
        </p>
      ) : (
        <>
          <p data-testid="model-settings-in-force" style={MUTED}>
            {page.data.version
              ? t("modelSettings.inForce", { version: short(page.data.version) })
              : t("modelSettings.inForceNone")}
          </p>
          <Tabs
            tabs={[
              { id: "edit", label: t("modelSettings.tab.edit") },
              { id: "history", label: t("modelSettings.tab.history") },
              { id: "keys", label: t("modelSettings.tab.keys") },
            ]}
            value={tab}
            onChange={setTab}
            label={t("modelSettings.tabs")}
            idPrefix="model-settings"
          />
          <div role="tabpanel" id={`model-settings-panel-${tab}`} aria-labelledby={`model-settings-tab-${tab}`}>
            {tab === "edit" && (
              <EditTab
                client={client}
                defaults={page.data.defaults}
                draft={page.data.draft}
                published={page.data.published}
              />
            )}
            {tab === "history" && (
              <HistoryTab client={client} current={page.data.version} published={page.data.published} />
            )}
            {tab === "keys" && <KeysTab client={client} keys={page.data.keys} />}
          </div>
        </>
      )}
    </div>
  );
}

const KIND = {
  added: "modelSettings.added",
  removed: "modelSettings.removed",
  changed: "modelSettings.changed",
} as const;

function ChangeList({ changes }: { changes: Change[] }) {
  const t = useT();
  return (
    <ul data-testid="model-settings-diff" style={{ ...MONO, margin: 0, paddingLeft: 18 }}>
      {changes.map((c) => (
        <li key={c.path}>
          <strong>{t(KIND[c.kind])}</strong> {c.path}
          {c.kind === "changed" && `: ${shown(c.before)} → ${shown(c.after)}`}
          {c.kind === "added" && `: ${shown(c.after)}`}
          {c.kind === "removed" && ` (${shown(c.before)})`}
        </li>
      ))}
    </ul>
  );
}

function EditTab({
  client,
  defaults,
  draft,
  published,
}: {
  client: Client;
  defaults: SettingsDocument;
  draft: Patch;
  published: Patch;
}) {
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const saved = useMemo(() => pretty(draft), [draft]);
  const [text, setText] = useState(saved);
  const [checkedText, setCheckedText] = useState(saved);
  const [notice, setNotice] = useState("");
  const [refused, setRefused] = useState<string[]>([]);
  // A save, an Apply or a rollback hands back a new draft; the editor follows it.
  useEffect(() => setText(saved), [saved]);
  useEffect(() => {
    const timer = setTimeout(() => setCheckedText(text), CHECK_AFTER_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const parsed = parsePatch(text);
  const checkedParse = parsePatch(checkedText);
  const check = useQuery({
    queryKey: [...qk.modelSettings, "check", checkedText],
    queryFn: () => client.check(checkedParse.ok ? checkedParse.patch : {}),
    enabled: checkedParse.ok,
  });
  const dirty = text !== saved;
  const patch = parsed.ok ? parsed.patch : null;

  const save = useMutation({
    meta: { silentError: true },
    mutationFn: (p: Patch) => client.saveDraft(p),
    onSuccess: () => {
      setNotice(t("modelSettings.saved"));
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
  });
  const apply = useMutation({
    meta: { silentError: true },
    mutationFn: () => client.apply(),
    onSuccess: (applied: Applied) => {
      setRefused([]);
      setNotice(t("modelSettings.applied", { version: short(applied.version) }));
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
    onError: (e) => setRefused(problemsOf(e)),
  });

  const askToApply = async () => {
    const changes = diffPatches(published, draft);
    const choice = await dialog.confirm({
      title: t("modelSettings.applyTitle"),
      body: (
        <div style={{ display: "grid", gap: 8 }}>
          <p>{t("modelSettings.applyBody")}</p>
          {changes.length ? <ChangeList changes={changes} /> : <p>{t("modelSettings.applyNothing")}</p>}
        </div>
      ),
      actions: [
        { id: "cancel", label: t("modelSettings.cancel") },
        { id: "apply", label: t("modelSettings.applyConfirm"), variant: "primary" },
      ],
    });
    if (choice === "apply") apply.mutate();
  };

  const problems = refused.length ? refused : (check.data ?? []);
  const changed = patch ? flattenPatch(patch) : [];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <section aria-labelledby="model-settings-draft" style={{ display: "grid", gap: 6 }}>
        <h2 id="model-settings-draft">{t("modelSettings.editor")}</h2>
        <MonacoEditor
          ariaLabel={t("modelSettings.editor")}
          value={text}
          onChange={(v) => {
            setText(v);
            setNotice("");
            setRefused([]);
          }}
          language="json"
          autoHeight
          minHeight={240}
        />
        {!parsed.ok ? (
          <div role="alert" data-testid="model-settings-parse" className="ev-banner ev-banner--err">
            <div className="ev-banner__body">
              {parsed.error === "not-an-object"
                ? t("modelSettings.notObject")
                : t("modelSettings.notJson", { error: parsed.error })}
            </div>
          </div>
        ) : problems.length ? (
          <div role="alert" data-testid="model-settings-problems" className="ev-banner ev-banner--err">
            <div className="ev-banner__body">
              {t("modelSettings.problems")}
              <ul style={{ ...MONO, margin: "4px 0 0", paddingLeft: 18 }}>
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : (
          <p data-testid="model-settings-ok" style={MUTED}>
            {check.isFetching || text !== checkedText ? t("modelSettings.checking") : t("modelSettings.noProblems")}
          </p>
        )}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-testid="model-settings-save"
            disabled={!patch || !dirty || save.isPending}
            onClick={() => patch && save.mutate(patch)}
          >
            {t("modelSettings.save")}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            data-testid="model-settings-apply"
            disabled={dirty || apply.isPending}
            onClick={() => void askToApply()}
          >
            {t("modelSettings.apply")}
          </button>
          {dirty && <span style={MUTED}>{t("modelSettings.saveFirst")}</span>}
          {notice && (
            <span data-testid="model-settings-notice" style={MUTED}>
              {notice}
            </span>
          )}
        </div>
      </section>

      <section aria-labelledby="model-settings-changes" style={{ display: "grid", gap: 6 }}>
        <h2 id="model-settings-changes">{t("modelSettings.changes")}</h2>
        {changed.length === 0 ? (
          <p style={MUTED}>{t("modelSettings.changesNone")}</p>
        ) : (
          <ul data-testid="model-settings-changes" style={{ ...MONO, margin: 0, paddingLeft: 0, listStyle: "none" }}>
            {changed.map(([path, value]) => {
              const fallback = valueAt(defaults, path);
              return (
                <li key={path} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                  <span>
                    {path}: {shown(value)}
                  </span>
                  <span style={MUTED}>
                    {fallback.found
                      ? t("modelSettings.default", { value: shown(fallback.value) })
                      : t("modelSettings.defaultNone")}
                  </span>
                  <button
                    type="button"
                    className="btn"
                    data-variant="ghost"
                    data-size="sm"
                    data-testid={`model-settings-reset-${path}`}
                    onClick={() => patch && setText(pretty(withoutPath(patch, path)))}
                  >
                    {t("modelSettings.reset")}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <TestConnection client={client} defaults={defaults} draft={draft} />

      <details>
        <summary>{t("modelSettings.defaults")}</summary>
        <MonacoEditor
          ariaLabel={t("modelSettings.defaults")}
          value={pretty(defaults)}
          language="json"
          readOnly
          autoHeight
          minHeight={240}
        />
      </details>
    </div>
  );
}

function TestConnection({ client, defaults, draft }: { client: Client; defaults: SettingsDocument; draft: Patch }) {
  const t = useT();
  const presets = useMemo(() => {
    const names = new Set<string>();
    for (const doc of [defaults, draft] as Record<string, unknown>[]) {
      const found = valueAt(doc as SettingsDocument, "config.agents.presets");
      if (found.found && found.value && typeof found.value === "object")
        for (const [name, v] of Object.entries(found.value)) if (v !== null) names.add(name);
    }
    return [...names].sort();
  }, [defaults, draft]);
  const [preset, setPreset] = useState("");
  const test = useMutation({ meta: { silentError: true }, mutationFn: (p: string) => client.testConnection(p) });
  const chosen = preset || presets[0] || "";

  return (
    <section aria-labelledby="model-settings-test" style={{ display: "grid", gap: 6 }}>
      <h2 id="model-settings-test">{t("modelSettings.test")}</h2>
      <p style={MUTED}>{t("modelSettings.testHint")}</p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select
          className="input"
          aria-label={t("modelSettings.testPreset")}
          value={chosen}
          onChange={(e) => setPreset(e.target.value)}
        >
          {presets.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-testid="model-settings-test"
          disabled={!chosen || test.isPending}
          onClick={() => test.mutate(chosen)}
        >
          {test.isPending ? "…" : t("modelSettings.test")}
        </button>
      </div>
      {test.data && (
        <p data-testid="model-settings-test-result" role={test.data.ok ? undefined : "alert"} style={MONO}>
          {test.data.ok
            ? t("modelSettings.testOk", { model: test.data.model, ms: String(test.data.ms), reply: test.data.reply })
            : t("modelSettings.testFailed", { error: test.data.error })}
        </p>
      )}
      {test.isError && (
        <p role="alert" style={MONO}>
          {t("modelSettings.testFailed", { error: String(test.error) })}
        </p>
      )}
    </section>
  );
}

function HistoryTab({ client, current, published }: { client: Client; current: string | null; published: Patch }) {
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: qk.modelSettingsHistory, queryFn: () => client.history() });
  const [refused, setRefused] = useState<string[]>([]);
  const rollback = useMutation({
    meta: { silentError: true },
    mutationFn: (version: string) => client.rollback(version),
    onSuccess: () => {
      setRefused([]);
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
    onError: (e) => setRefused(problemsOf(e).length ? problemsOf(e) : [String(e)]),
  });

  const askToRollBack = async (entry: Applied) => {
    const changes = diffPatches(published, entry.patch);
    const choice = await dialog.confirm({
      title: t("modelSettings.rollbackTitle", { version: short(entry.version) }),
      body: (
        <div style={{ display: "grid", gap: 8 }}>
          <p>{t("modelSettings.rollbackBody")}</p>
          {changes.length ? <ChangeList changes={changes} /> : <p>{t("modelSettings.applyNothing")}</p>}
        </div>
      ),
      actions: [
        { id: "cancel", label: t("modelSettings.cancel") },
        { id: "rollback", label: t("modelSettings.rollbackConfirm"), variant: "primary" },
      ],
    });
    if (choice === "rollback") rollback.mutate(entry.version);
  };

  if (history.isPending) return <p style={MUTED}>…</p>;
  if (history.isError)
    return (
      <p role="alert" style={MUTED}>
        {t("modelSettings.loadFailed")}
      </p>
    );
  if (history.data.length === 0)
    return (
      <p data-testid="model-settings-history-empty" style={MUTED}>
        {t("modelSettings.historyEmpty")}
      </p>
    );
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {refused.length > 0 && (
        <div role="alert" className="ev-banner ev-banner--err">
          <div className="ev-banner__body">
            <ul style={{ ...MONO, margin: 0, paddingLeft: 18 }}>
              {refused.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <ol data-testid="model-settings-history" style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 6 }}>
        {history.data.map((entry, i) => {
          // The newest entry carrying the current content id is what is in force.
          const inForce = entry.version === current && history.data.findIndex((e) => e.version === current) === i;
          return (
            <li key={`${entry.version}-${entry.applied_at}`} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={MONO}>
                {t("modelSettings.historyRow", {
                  version: short(entry.version),
                  when: new Date(entry.applied_at * 1000).toLocaleString(),
                })}
              </span>
              <span style={MUTED}>{entry.by}</span>
              {inForce && (
                <span data-testid="model-settings-current" style={BADGE}>
                  {t("modelSettings.current")}
                </span>
              )}
              {entry.keys_changed.length > 0 && (
                <span style={MUTED}>{t("modelSettings.keysChanged", { paths: entry.keys_changed.join(", ") })}</span>
              )}
              {!inForce && (
                <button
                  type="button"
                  className="btn"
                  data-variant="secondary"
                  data-size="sm"
                  data-testid={`model-settings-rollback-${i}`}
                  disabled={rollback.isPending}
                  onClick={() => void askToRollBack(entry)}
                >
                  {t("modelSettings.rollback")}
                </button>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function KeysTab({ client, keys }: { client: Client; keys: Record<string, string> }) {
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const [value, setValue] = useState("");
  const [refused, setRefused] = useState<string[]>([]);
  const done = () => {
    setRefused([]);
    void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
  };
  const set = useMutation({
    meta: { silentError: true },
    mutationFn: () => client.setKey(path.trim(), value),
    onSuccess: () => {
      setPath("");
      setValue("");
      done();
    },
    onError: (e) => setRefused(problemsOf(e).length ? problemsOf(e) : [String(e)]),
  });
  const clear = useMutation({ meta: { silentError: true }, mutationFn: (p: string) => client.clearKey(p), onSuccess: done });

  const askToClear = async (p: string) => {
    const choice = await dialog.confirm({
      title: t("modelSettings.keyClearTitle", { path: p }),
      body: t("modelSettings.keyClearBody"),
      actions: [
        { id: "cancel", label: t("modelSettings.cancel") },
        { id: "clear", label: t("modelSettings.keyClear"), variant: "danger" },
      ],
    });
    if (choice === "clear") clear.mutate(p);
  };

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <p style={MUTED}>{t("modelSettings.keysDesc")}</p>
      {Object.keys(keys).length === 0 ? (
        <p data-testid="model-settings-keys-empty" style={MUTED}>
          {t("modelSettings.keysNone")}
        </p>
      ) : (
        <ul data-testid="model-settings-keys" style={{ ...MONO, margin: 0, paddingLeft: 0, listStyle: "none" }}>
          {Object.entries(keys).map(([p, tail]) => (
            <li key={p} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <span>{p}</span>
              <span style={MUTED}>{tail}</span>
              <button
                type="button"
                className="btn"
                data-variant="danger"
                data-size="sm"
                data-testid={`model-settings-key-clear-${p}`}
                onClick={() => void askToClear(p)}
              >
                {t("modelSettings.keyClear")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <input
          className="input"
          data-testid="model-settings-key-path"
          aria-label={t("modelSettings.keyPath")}
          placeholder={t("modelSettings.keyPath")}
          value={path}
          onChange={(e) => setPath(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          style={{ ...MONO, minWidth: "min(100%, 28rem)" }}
        />
        <input
          className="input"
          data-masked
          data-testid="model-settings-key-value"
          aria-label={t("modelSettings.keyValue")}
          placeholder={t("modelSettings.keyValue")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          onCopy={blockMaskedCopy}
          onCut={blockMaskedCopy}
          onDragStart={blockMaskedCopy}
          style={MONO}
        />
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-testid="model-settings-key-set"
          disabled={!path.trim() || !value || set.isPending}
          onClick={() => set.mutate()}
        >
          {t("modelSettings.keySet")}
        </button>
      </div>
      {refused.length > 0 && (
        <p role="alert" data-testid="model-settings-key-refused" style={MONO}>
          {refused.join(" · ")}
        </p>
      )}
    </div>
  );
}
