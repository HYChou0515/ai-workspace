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
import { useEffect, useMemo, useRef, useState } from "react";

import {
  modelSettingsApi,
  problemsOf,
  type Applied,
  type ModelSettingsClient,
  type ModelSettingsPage as PageData,
  type SettingsDocument,
} from "../api/modelSettings";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import { useDialog } from "../components/Dialog";
import { MonacoEditor, type EditorMarker } from "../components/MonacoEditor";
import { Tabs } from "../components/ShareTabs";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useIsSuperuserState } from "../hooks/useIsSuperuser";
import { useT } from "../lib/i18n";
import { sameShape } from "../lib/sameShape";
import { blockMaskedCopy } from "../lib/maskedSecret";
import { pxToRem } from "../lib/pxToRem";
import {
  diffEffective,
  flattenParts,
  lineOfPath,
  parsePatch,
  valueAt,
  withoutPath,
  type Change,
  type Patch,
} from "../lib/settingsPatch";

const MONO = { fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: pxToRem(12) };
const MUTED = { fontSize: pxToRem(12), color: "var(--text-paper-d)" } as const;
/** A status the operator acts on: body size and colour, not muted. */
const STATUS = { fontSize: pxToRem(13), color: "var(--text-paper)", margin: 0 } as const;
const H2 = { margin: 0 } as const;
const LABEL = { fontSize: pxToRem(12), color: "var(--text-paper)" } as const;
/** The "in force" tag on a history row — the tokens the skill hub's badge uses. */
/** A bullet item, not a row: overrides the site-wide `.page ul > li` row style. */
const BULLET = { display: "list-item", borderTop: "none", padding: 0 } as const;
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
const messageOf = (e: unknown) => (e instanceof Error ? e.message : String(e));
const short = (version: string) => version.slice(0, 8);
// An empty text is said as `""`: printed as nothing it read as missing data
// (final review 4, NN/g heuristic #1).
const shown = (value: unknown) =>
  typeof value === "string" && value !== "" ? value : JSON.stringify(value);
/** A value may be a whole preset on one line: it wraps rather than widen the
 * page or clip a dialog (final review 4; NN/g "Beware Horizontal Scrolling"). */
const WRAP = { overflowWrap: "anywhere", minWidth: 0 } as const;

type Client = Pick<
  ModelSettingsClient,
  "get" | "saveDraft" | "check" | "apply" | "history" | "rollback" | "setKey" | "clearKey" | "testConnection"
>;

export function ModelSettingsPage({ client = modelSettingsApi }: { client?: Client }) {
  const t = useT();
  const { isSuperuser, ready } = useIsSuperuserState();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("modelSettings.title") }]);
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
    <div className="page" style={{ display: "grid", gap: 16 }}>
      {/* One header block — NN/g "Proximity Principle": the title, what the page
          does and what is in force read as one group. */}
      <header style={{ display: "grid", gap: 6 }}>
        <h1 style={{ margin: 0 }}>{t("modelSettings.title")}</h1>
        <p style={{ ...MUTED, margin: 0 }}>{t("modelSettings.desc")}</p>
        {page.data && (
          <p data-testid="model-settings-in-force" style={{ ...STATUS, margin: 0 }}>
            {page.data.version
              ? t("modelSettings.inForce", { version: short(page.data.version) })
              : t("modelSettings.inForceNone")}
          </p>
        )}
      </header>
      {/* Data in hand is kept: a failed REFRESH must not swap the page — and
          the unsaved edits in it — for "load failed" (final review 4). */}
      {page.data ? (
        <>
          {page.isError && (
            <p role="alert" data-testid="model-settings-refresh-failed" style={{ ...MUTED, margin: 0 }}>
              {t("modelSettings.refreshFailed")}
            </p>
          )}
          <Loaded client={client} data={page.data} />
        </>
      ) : page.isError ? (
        <p role="alert" style={MUTED}>
          {t("modelSettings.loadFailed")}
        </p>
      ) : (
        <p style={MUTED}>…</p>
      )}
    </div>
  );
}

const KIND = {
  added: "modelSettings.added",
  removed: "modelSettings.removed",
  changed: "modelSettings.changed",
} as const;

function ChangeList({ changes, testId = "model-settings-diff" }: { changes: Change[]; testId?: string }) {
  const t = useT();
  return (
    <ul data-testid={testId} style={{ ...MONO, margin: 0, paddingLeft: 18 }}>
      {changes.map((c) => (
        <li key={c.path} style={{ ...BULLET, ...WRAP }}>
          <strong>{t(KIND[c.kind])}</strong> {c.path}
          {c.kind === "changed" && `: ${shown(c.before)} → ${shown(c.after)}`}
          {c.kind === "added" && `: ${shown(c.after)}`}
          {c.kind === "removed" && ` (${shown(c.before)})`}
        </li>
      ))}
    </ul>
  );
}

/** Problems name a dotted path first ("config.server: …", "config: …"). */
const pathOf = (problem: string) => problem.split(":", 1)[0].trim();

/** Where JSON.parse says it gave up, as a line — when the message says. */
function lineOfParseError(text: string, error: string): number | null {
  const at = /position (\d+)/.exec(error);
  if (!at) return null;
  return text.slice(0, Number(at[1])).split("\n").length;
}

function KeyChangeList({ pending, keys }: { pending: string[]; keys: Record<string, string> }) {
  const t = useT();
  return (
    <ul data-testid="model-settings-key-diff" style={{ ...MONO, margin: 0, paddingLeft: 18 }}>
      {pending.map((path) => (
        <li key={path} style={BULLET}>
          <strong>{t(path in keys ? "modelSettings.keyChangeSet" : "modelSettings.keyChangeCleared")}</strong>{" "}
          {path}
          {path in keys && ` (${keys[path] || t("modelSettings.keyIsSet")})`}
        </li>
      ))}
    </ul>
  );
}

/** The page's one draft: the editor's text, its checks, and the Apply that
 * puts the saved draft and the pending keys in force. Held above the tabs, so
 * looking at the keys or the history does not throw unsaved edits away
 * (review r2), and Apply is the same flow from the Edit and the API keys tab
 * (NN/g "Tabs, Used Right": a task must not need two tabs at once). */
const UNSAVED_KEY = "model-settings.unsaved";

type Unsaved = { text: string; base: Patch };

/** The unsaved edits this session left, unless they say what is saved. Storage
 * can be missing or refuse (a private window): then there is nothing kept. */
function readUnsaved(saved: string): Unsaved | null {
  try {
    const raw = sessionStorage.getItem(UNSAVED_KEY);
    if (!raw) return null;
    const kept = JSON.parse(raw) as Unsaved;
    return typeof kept.text === "string" && kept.text !== saved ? kept : null;
  } catch {
    return null;
  }
}

function writeUnsaved(kept: Unsaved | null) {
  try {
    if (kept) sessionStorage.setItem(UNSAVED_KEY, JSON.stringify(kept));
    else sessionStorage.removeItem(UNSAVED_KEY);
  } catch {
    // storage refused: the edits live only in the page, as before
  }
}

function useDraft(client: Client, data: PageData) {
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const { defaults, draft, published, keys } = data;
  const pendingKeys = data.pending_keys ?? [];
  const keyRevision = data.key_revision;
  const saved = useMemo(() => pretty(draft), [draft]);
  // Unsaved edits outlive the page for this browser session: the router here
  // cannot hold a navigation back to ask, so leaving and coming back gives
  // them back instead — NN/g's undo in place of a confirmation (final review 9).
  const [kept] = useState(() => readUnsaved(saved));
  const [text, setText] = useState(kept?.text ?? saved);
  const [restored, setRestored] = useState(kept !== null);
  const [checkedText, setCheckedText] = useState(saved);
  const [notice, setNotice] = useState("");
  const [refused, setRefused] = useState<string[]>([]);
  const [saveError, setSaveError] = useState("");
  // An Apply that failed without naming a problem (a 409, a 500, the network):
  // said beside Apply and retryable — not a problem to fix in the draft.
  const [applyError, setApplyError] = useState("");
  // A save or a rollback hands back a new draft; the editor follows it — but
  // only while it still says what the LAST saved draft said. Unsaved edits are
  // never replaced by a refetch (confirmation round: another superuser's save,
  // picked up by any refetch, replaced the text being typed without a word).
  const previous = useRef(draft);
  // The saved draft the text was taken from — what Save says it builds on, so
  // the server refuses (409) to overwrite a newer save unseen (final review 8:
  // Apply was guarded against that race, Save was not). Once told, the next
  // Save builds on the newest one: replacing it is then a choice.
  const base = useRef<Patch>(kept?.base ?? draft);
  const told = useRef(false);
  const textNow = useRef(text);
  textNow.current = text;
  useEffect(() => {
    const was = previous.current;
    previous.current = draft;
    const now = parsePatch(textNow.current);
    if (now.ok && sameShape(now.patch, was)) setText(saved);
    if (now.ok && (sameShape(now.patch, was) || sameShape(now.patch, draft))) {
      base.current = draft;
      told.current = false;
    }
  }, [draft, saved]);
  useEffect(() => {
    const timer = setTimeout(() => setCheckedText(text), CHECK_AFTER_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const parsed = parsePatch(text);
  // What the page SAYS about the text waits for a pause in typing, like the
  // check — an error at every keystroke is the "too early" NN/g warns against.
  const settled = parsePatch(checkedText);
  const check = useQuery({
    queryKey: [...qk.modelSettings, "check", checkedText],
    queryFn: () => client.check(settled.ok ? settled.patch : {}),
    enabled: settled.ok,
  });
  // By shape, never by text (CLAUDE.md `sameShape`; review r2: the same content
  // in other spacing stayed "unsaved" for good).
  const dirty = !parsed.ok || !sameShape(parsed.patch, draft);
  const checking = text !== checkedText || check.isFetching;

  const save = useMutation({
    meta: { silentError: true },
    mutationFn: (p: Patch) => client.saveDraft(p, told.current ? previous.current : base.current),
    onError: (e) => {
      setSaveError(problemsOf(e).join(" · ") || messageOf(e));
      if (e instanceof HttpError && e.status === 409) {
        told.current = true;
        void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
      }
    },
    // What it builds on next is what it just saved — not left to the refetch:
    // typing on before that arrives kept the old base, and the next Save got a
    // false "someone else saved" (final review 11).
    onSuccess: (_problems, p) => {
      base.current = p;
      told.current = false;
      setSaveError("");
      setNotice(t("modelSettings.saved"));
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
  });
  const apply = useMutation({
    meta: { silentError: true },
    // What the dialog LISTED goes with it — captured when it opened, not read
    // from a later render (final review) — and the server refuses anything else.
    mutationFn: (seen: { draft: Patch; pendingKeys: string[]; keyRevision: string }) =>
      client.apply(seen.draft, seen.pendingKeys, seen.keyRevision),
    onMutate: () => setApplyError(""),
    onSuccess: (applied: Applied) => {
      setRefused([]);
      setNotice(t("modelSettings.applied", { version: short(applied.version) }));
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
    // A refusal names its problems, which block Apply until the draft changes.
    // Any other failure (a 409, a 500, the network) is still a reason the person
    // must see — the mutation is `silentError` — but nothing in the draft to fix:
    // it is said beside Apply, which stays offered, and the page is read again
    // so a 409 shows what changed (final review 4).
    onError: (e) => {
      if (problemsOf(e).length) {
        setRefused(problemsOf(e));
        return;
      }
      setApplyError(messageOf(e));
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
  });

  // What Apply would change in what RUNS — the SAVED draft is what it publishes.
  const changes = useMemo(
    () => diffEffective((defaults ?? {}) as Patch, published, draft),
    [defaults, published, draft],
  );
  const problems = refused.length ? refused : (check.data ?? []);
  const nothingToApply = changes.length === 0 && pendingKeys.length === 0;
  // A check that FAILED is not a check that found nothing (review r1).
  const checkFailed = check.isError && !checking;
  // NN/g heuristic #1: Apply is offered only when it would do something. Why
  // not is a KIND, worded by the tab that shows it (review r3: the keys tab
  // pointed at "the problems listed above" it does not list). The JSON reason
  // waits for a pause in typing, like the banner (NN/g: not before input ends).
  const blocked: Blocked = !settled.ok
    ? "json"
    : dirty
      ? "dirty"
      : checkFailed && !refused.length
        ? "checkFailed"
        : problems.length
          ? "problems"
          : nothingToApply
            ? "nothing"
            : "";

  const askToApply = async () => {
    const seen = { draft, pendingKeys, keyRevision };
    const choice = await dialog.confirm({
      title: t("modelSettings.applyTitle"),
      body: (
        <div style={{ display: "grid", gap: 8 }}>
          <p style={{ margin: 0 }}>{t("modelSettings.applyBody")}</p>
          {changes.length > 0 && <ChangeList changes={changes} />}
          {pendingKeys.length > 0 && <KeyChangeList pending={pendingKeys} keys={keys} />}
        </div>
      ),
      actions: [
        { id: "cancel", label: t("modelSettings.cancel") },
        { id: "apply", label: t("modelSettings.applyConfirm"), variant: "primary" },
      ],
    });
    if (choice === "apply") apply.mutate(seen);
  };

  const edit = (v: string) => {
    setText(v);
    setNotice("");
    setRefused([]);
    setSaveError("");
    setApplyError("");
  };
  // NN/g heuristic #3 (user control): one button gives the saved draft back —
  // the editor's own undo is gone once a tab switch unmounts it (final review).
  // Hand-written JSON is work: Discard asks once (NN/g "Confirmation
  // Dialogs"; M3's example is "Discard draft?"), as a modal's close does.
  // Kept while there is something unsaved; a reload or a closed tab asks
  // first (the browser's own prompt).
  useEffect(() => {
    writeUnsaved(dirty ? { text, base: base.current } : null);
    if (!dirty) return;
    const ask = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", ask);
    return () => window.removeEventListener("beforeunload", ask);
  }, [dirty, text]);
  const discard = useDirtyClose(true, () => {
    base.current = draft;
    told.current = false;
    setRestored(false);
    edit(saved);
  });
  // "Applied: version X" describes the last Apply; a key change or a rollback
  // since makes it stale beside an Apply with work waiting (review r3).
  const clearNotice = () => setNotice("");

  return {
    text,
    edit,
    parsed,
    settled,
    checkedText,
    dirty,
    checking,
    check,
    checkFailed,
    problems,
    refused,
    notice,
    saveError,
    applyError,
    save,
    apply,
    changes,
    pendingKeys,
    nothingToApply,
    blocked,
    askToApply,
    clearNotice,
    discard,
    restored,
  };
}

type Draft = ReturnType<typeof useDraft>;
type Blocked = "" | "json" | "dirty" | "checkFailed" | "problems" | "nothing";

const WHY = {
  edit: {
    json: "modelSettings.fixJsonFirst",
    dirty: "modelSettings.saveFirst",
    checkFailed: "modelSettings.applyBlockedCheck",
    problems: "modelSettings.applyBlockedProblems",
    nothing: "modelSettings.nothingToApply",
  },
  // the keys tab cannot save the draft nor show its problems — it says where
  keys: {
    json: "modelSettings.keysBlockedJson",
    dirty: "modelSettings.keysBlockedDirty",
    checkFailed: "modelSettings.keysBlockedCheck",
    problems: "modelSettings.keysBlockedProblems",
    nothing: "modelSettings.nothingToApply",
  },
} as const;

function ApplyButton({
  draft,
  testId,
  explain,
  where,
  goEdit,
}: {
  draft: Draft;
  testId: string;
  explain: boolean;
  where: "edit" | "keys";
  goEdit?: () => void;
}) {
  const t = useT();
  const blocked = draft.blocked;
  return (
    <>
      <button
        type="button"
        className="btn"
        data-variant="primary"
        data-testid={testId}
        disabled={blocked !== "" || draft.apply.isPending}
        onClick={() => void draft.askToApply()}
      >
        {t("modelSettings.apply")}
      </button>
      {explain && blocked && (
        <span data-testid={`${testId}-why`} style={MUTED}>
          {t(WHY[where][blocked])}
        </span>
      )}
      {draft.applyError && (
        <span role="alert" data-testid={`${testId}-failed`} style={{ ...MUTED, ...WRAP, color: "var(--err)" }}>
          {t("modelSettings.applyFailed", { error: draft.applyError })}
        </span>
      )}
      {explain && goEdit && blocked && blocked !== "nothing" && (
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="model-settings-go-edit"
          onClick={goEdit}
        >
          {t("modelSettings.goEdit")}
        </button>
      )}
      {draft.notice && (
        <span role="status" data-testid="model-settings-notice" style={MUTED}>
          {draft.notice}
        </span>
      )}
    </>
  );
}

function Loaded({ client, data }: { client: Client; data: PageData }) {
  const t = useT();
  const [tab, setTab] = useState("edit");
  const draft = useDraft(client, data);
  return (
    <>
      <Tabs
        tabs={[
          { id: "edit", label: t(draft.dirty ? "modelSettings.tab.editUnsaved" : "modelSettings.tab.edit") },
          { id: "history", label: t("modelSettings.tab.history") },
          { id: "keys", label: t("modelSettings.tab.keys") },
        ]}
        value={tab}
        onChange={setTab}
        label={t("modelSettings.tabs")}
        idPrefix="model-settings"
      />
      <div role="tabpanel" id={`model-settings-panel-${tab}`} aria-labelledby={`model-settings-tab-${tab}`}>
        {tab === "edit" && <EditTab client={client} defaults={data.defaults} saved={data.draft} draft={draft} />}
        {tab === "history" && (
          <HistoryTab
            client={client}
            current={data.version}
            published={data.published}
            defaults={data.defaults}
            draft={draft}
            goEdit={() => setTab("edit")}
          />
        )}
        {tab === "keys" && (
          <KeysTab
            client={client}
            keys={data.keys}
            applied={data.applied_keys}
            draft={draft}
            goEdit={() => setTab("edit")}
          />
        )}
      </div>
    </>
  );
}

function EditTab({
  client,
  defaults,
  saved,
  draft,
}: {
  client: Client;
  defaults: SettingsDocument;
  saved: Patch;
  draft: Draft;
}) {
  const t = useT();
  const { text, parsed, settled, checkedText } = draft;

  // Each problem drawn on the line it names (NN/g "Error-Message Guidelines":
  // close to its source); the list under the editor stays as the summary.
  const markers: EditorMarker[] = !settled.ok
    ? [{ line: lineOfParseError(checkedText, settled.error) ?? 1, message: settled.error }]
    : draft.problems.flatMap((p) => {
        const line = lineOfPath(text, pathOf(p));
        return line === null ? [] : [{ line, message: p }];
      });
  const patch = parsed.ok ? parsed.patch : null;
  const changed = patch ? flattenParts(patch) : [];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section aria-labelledby="model-settings-draft" style={{ display: "grid", gap: 8 }}>
        <h2 id="model-settings-draft" style={H2}>
          {t("modelSettings.editor")}
        </h2>
        {draft.restored && draft.dirty && (
          <p data-testid="model-settings-restored" role="status" style={STATUS}>
            {t("modelSettings.restored")}
          </p>
        )}
        <MonacoEditor
          ariaLabel={t("modelSettings.editor")}
          value={text}
          onChange={draft.edit}
          language="json"
          autoHeight
          minHeight={240}
          markers={markers}
        />
        {/* Polite, not an alert: it is re-evaluated at every pause in typing, and
            an interrupting announcement mid-brace is the "too early" error NN/g
            warns against. */}
        <div aria-live="polite">
          {!settled.ok ? (
            <div data-testid="model-settings-parse" className="ev-banner ev-banner--err">
              <div className="ev-banner__body">
                {settled.error === "not-an-object"
                  ? t("modelSettings.notObject")
                  : t("modelSettings.notJson", { error: settled.error })}
              </div>
            </div>
          ) : draft.checkFailed && !draft.refused.length ? (
            <div data-testid="model-settings-check-failed" className="ev-banner ev-banner--err">
              <div className="ev-banner__body">
                {t("modelSettings.checkFailed", { error: messageOf(draft.check.error) })}
              </div>
            </div>
          ) : draft.problems.length ? (
            <div data-testid="model-settings-problems" className="ev-banner ev-banner--err">
              <div className="ev-banner__body">
                {t("modelSettings.problems")}
                <ul style={{ ...MONO, margin: "4px 0 0", paddingLeft: 18 }}>
                  {draft.problems.map((p) => (
                    <li key={p} style={BULLET}>
                      {p}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <p data-testid="model-settings-status" style={STATUS}>
              {draft.checking
                ? t("modelSettings.checking")
                : draft.dirty
                  ? t("modelSettings.noProblems")
                  : draft.nothingToApply
                    ? t("modelSettings.nothingToApply")
                    : t("modelSettings.readyToApply", {
                        values: String(draft.changes.length),
                        keys: String(draft.pendingKeys.length),
                      })}
            </p>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-testid="model-settings-save"
            disabled={!patch || !draft.dirty || draft.save.isPending}
            onClick={() => patch && draft.save.mutate(patch)}
          >
            {t("modelSettings.save")}
          </button>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-testid="model-settings-discard"
            disabled={!draft.dirty}
            onClick={draft.discard}
          >
            {t("modelSettings.discard")}
          </button>
          {/* The status line above already says "nothing to apply" / "problems";
              the reason beside the button is for what it does not say. */}
          <ApplyButton draft={draft} testId="model-settings-apply" explain={draft.dirty} where="edit" />
          {draft.saveError && (
            <span role="alert" data-testid="model-settings-save-failed" style={{ ...MUTED, color: "var(--err)" }}>
              {t("modelSettings.saveFailed", { error: draft.saveError })}
            </span>
          )}
        </div>
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
      </section>

      <section aria-labelledby="model-settings-changes" style={{ display: "grid", gap: 8 }}>
        <h2 id="model-settings-changes" style={H2}>
          {t("modelSettings.changes")}
        </h2>
        {!parsed.ok ? (
          // An unparsable text is not an empty patch (final review): say what is
          // known instead of claiming everything is the deploy default.
          <p style={{ ...MUTED, margin: 0 }}>{t("modelSettings.changesUnknown")}</p>
        ) : changed.length === 0 ? (
          <p style={{ ...MUTED, margin: 0 }}>{t("modelSettings.changesNone")}</p>
        ) : (
          // Monospace on the paths and values only: a button in the list keeps
          // the page's font, like every other button (final review 7; NN/g
          // heuristic #4, consistency).
          <ul
            data-testid="model-settings-changes"
            style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 6 }}
          >
            {changed.map(({ path, parts, value }) => {
              const fallback = valueAt(defaults, parts);
              return (
                <li key={path} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ ...MONO, ...WRAP }}>
                    {path}: {shown(value)}
                  </span>
                  <span style={{ ...MONO, ...MUTED, ...WRAP }}>
                    {fallback.found
                      ? t("modelSettings.default", { value: shown(fallback.value) })
                      : t("modelSettings.defaultNone")}
                  </span>
                  {/* Outlined, not ghost: NN/g "Beyond Blue Links" — a flat
                      control must still look clickable; M3 outlined = medium emphasis. */}
                  <button
                    type="button"
                    className="btn"
                    data-variant="secondary"
                    data-size="sm"
                    data-testid={`model-settings-reset-${path}`}
                    onClick={() => patch && draft.edit(pretty(withoutPath(patch, parts)))}
                  >
                    {t("modelSettings.reset")}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <TestConnection client={client} defaults={defaults} draft={saved} />
    </div>
  );
}

function TestConnection({ client, defaults, draft }: { client: Client; defaults: SettingsDocument; draft: Patch }) {
  const t = useT();
  // The presets the draft has: the defaults', plus what it adds, minus what it
  // hides (`null`, M12) — a hidden one would only answer "no preset named …".
  const presets = useMemo(() => {
    const at = (doc: SettingsDocument) => {
      const found = valueAt(doc, ["config", "agents", "presets"]);
      return found.found && found.value && typeof found.value === "object"
        ? Object.entries(found.value as Record<string, unknown>)
        : [];
    };
    const names = new Set(at(defaults).map(([name]) => name));
    for (const [name, v] of at(draft as SettingsDocument)) {
      if (v === null) names.delete(name);
      else names.add(name);
    }
    return [...names].sort();
  }, [defaults, draft]);
  const [preset, setPreset] = useState("");
  const test = useMutation({ meta: { silentError: true }, mutationFn: (p: string) => client.testConnection(p) });
  const chosen = preset || presets[0] || "";
  // A call can take tens of seconds on a slow model: say it is running and
  // for how long, under a button that keeps its label (final review 7; NN/g
  // "Progress Indicators Make a Slow System Less Insufferable").
  const [waited, setWaited] = useState(0);
  // The section sits low on the page: its progress and result are brought into
  // view, as a rollback's result is (final review 15).
  const testAt = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    testAt.current?.scrollIntoView?.({ block: "nearest" });
  }, [test.isPending, test.data]);
  useEffect(() => {
    if (!test.isPending) return;
    setWaited(0);
    const started = Date.now();
    const timer = setInterval(() => setWaited(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [test.isPending]);

  return (
    <section aria-labelledby="model-settings-test" style={{ display: "grid", gap: 8 }}>
      <h2 id="model-settings-test" style={H2}>
        {t("modelSettings.test")}
      </h2>
      <p style={{ ...MUTED, margin: 0 }}>{t("modelSettings.testHint")}</p>
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
          {t("modelSettings.test")}
        </button>
      </div>
      {/* Its own line, so the picker beside the button stays put (final review 11). */}
      {test.isPending && (
        <p ref={testAt} role="status" data-testid="model-settings-test-running" style={{ ...MUTED, margin: 0 }}>
          {t("modelSettings.testRunning", { preset: test.variables ?? chosen, s: String(waited) })}
        </p>
      )}
      {test.data && (
        <p
          ref={testAt}
          data-testid="model-settings-test-result"
          role={test.data.ok ? undefined : "alert"}
          style={WRAP}
        >
          {test.data.ok ? (
            t("modelSettings.testOk", { model: test.data.model, ms: String(test.data.ms), reply: test.data.reply })
          ) : (
            // A plain sentence first, then the reason as given — often the
            // page's own refusal, sometimes the provider's words (NN/g
            // "Error-Message Guidelines"; final reviews 9–11).
            <>
              {t("modelSettings.testFailedLead", { preset: test.variables ?? chosen })}{" "}
              <span data-testid="model-settings-test-detail" style={MUTED}>
                {test.data.error}
              </span>
            </>
          )}
        </p>
      )}
      {test.isError && (
        <p role="alert" style={WRAP}>
          {t("modelSettings.testFailed", { error: String(test.error) })}
        </p>
      )}
    </section>
  );
}

function HistoryTab({
  client,
  current,
  published,
  defaults,
  draft,
  goEdit,
}: {
  client: Client;
  current: string | null;
  published: Patch;
  defaults: SettingsDocument;
  draft: Draft;
  goEdit: () => void;
}) {
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: qk.modelSettingsHistory, queryFn: () => client.history() });
  const [refused, setRefused] = useState<string[]>([]);
  const [rolled, setRolled] = useState<{ from: string; now: string } | null>(null);
  // Said where the person is: the button pressed may be far down a long list
  // on a phone, and the message sits above it (final review 10; NN/g
  // heuristic #1, visibility of system status).
  const rolledAt = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (rolled) rolledAt.current?.scrollIntoView?.({ block: "nearest" });
  }, [rolled]);
  const rollback = useMutation({
    meta: { silentError: true },
    mutationFn: (version: string) => client.rollback(version),
    // NN/g heuristic #1: say what happened — a rollback is a new version whose
    // id differs from the one named whenever the keys in force differ (M20).
    onSuccess: (applied: Applied, version: string) => {
      setRefused([]);
      setRolled({ from: version, now: applied.version });
      draft.clearNotice();
      void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
    },
    onError: (e) => setRefused(problemsOf(e).length ? problemsOf(e) : [messageOf(e)]),
  });

  const askToRollBack = async (entry: Applied) => {
    const changes = diffEffective((defaults ?? {}) as Patch, published, entry.patch);
    const choice = await dialog.confirm({
      title: t("modelSettings.rollbackTitle", { version: short(entry.version) }),
      body: (
        <div style={{ display: "grid", gap: 8 }}>
          <p>{t("modelSettings.rollbackBody")}</p>
          <ChangeList changes={changes} />
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
  // A rollback replaces the draft; over unsaved edits it would throw them away
  // without a word (review r3) — they are saved or undone first.
  // Said only where a rollback is offered — a reason for an action the page
  // does not show is noise (final review 15; NN/g heuristic #8).
  const rollable = history.data.some(
    (entry) => diffEffective((defaults ?? {}) as Patch, published, entry.patch).length > 0,
  );
  const blockedByEdits = draft.dirty;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {blockedByEdits && rollable && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <p data-testid="model-settings-rollback-why" style={{ ...STATUS, margin: 0 }}>
            {t("modelSettings.rollbackBlockedDirty")}
          </p>
          {/* A way there, as the keys tab gives (NN/g heuristic #4). */}
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="model-settings-go-edit"
            onClick={goEdit}
          >
            {t("modelSettings.goEdit")}
          </button>
        </div>
      )}
      {rolled && (
        <p ref={rolledAt} role="status" data-testid="model-settings-rolled-back" style={{ ...STATUS, margin: 0 }}>
          {t("modelSettings.rolledBack", { from: short(rolled.from), now: short(rolled.now) })}
        </p>
      )}
      {refused.length > 0 && (
        <div role="alert" className="ev-banner ev-banner--err">
          <div className="ev-banner__body">
            <ul style={{ ...MONO, margin: 0, paddingLeft: 18 }}>
              {refused.map((p) => (
                <li key={p} style={BULLET}>
                  {p}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <ol data-testid="model-settings-history" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
        {history.data.map((entry, i) => {
          // The newest entry carrying the current content id is what is in force.
          const inForce = entry.version === current && history.data.findIndex((e) => e.version === current) === i;
          // Offered only where it would change what runs — not on a version with
          // the values in force, the current one included (NN/g heuristic #8,
          // aesthetic and minimalist design: no action that does nothing).
          const changesSomething =
            diffEffective((defaults ?? {}) as Patch, published, entry.patch).length > 0;
          return (
            // What the version is, and its one action at the right edge — so the
            // button stays put whatever the row says, until the row is too narrow.
            <li
              key={`${entry.version}-${entry.applied_at}`}
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 12,
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 0",
                borderBottom: "1px solid var(--paper-3)",
              }}
            >
              {/* Wraps: on a phone the button drops below rather than squeeze
                  the changes into a narrow column (Material 3, compact window). */}
              <div style={{ display: "grid", gap: 2, minWidth: 0, flex: "1 1 18rem" }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                  <span style={MONO}>
                    {t("modelSettings.historyRow", {
                      when: new Date(entry.applied_at * 1000).toLocaleString(),
                      version: short(entry.version),
                    })}
                  </span>
                  <span style={MUTED}>{entry.by}</span>
                  {inForce && (
                    <span data-testid="model-settings-current" style={BADGE}>
                      {t("modelSettings.current")}
                    </span>
                  )}
                </div>
                <VersionChanges
                  defaults={defaults}
                  entry={entry}
                  before={history.data.slice(i + 1).find((e) => e.version === entry.previous)}
                  testId={`model-settings-version-changes-${i}`}
                />
                {entry.keys_changed.length > 0 && (
                  <span style={{ ...MUTED, overflowWrap: "anywhere" }}>
                    {t("modelSettings.keysChanged", { paths: entry.keys_changed.join(", ") })}
                  </span>
                )}
              </div>
              {changesSomething && (
                <button
                  type="button"
                  className="btn"
                  data-variant="secondary"
                  data-size="sm"
                  data-testid={`model-settings-rollback-${i}`}
                  disabled={rollback.isPending || blockedByEdits}
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

/** What a version changed from the one before it (M5): what RUNS before and
 * after, by `diffEffective`. The first version is compared with the deploy
 * defaults; one whose predecessor is beyond the history page says so. */
function VersionChanges({
  defaults,
  entry,
  before,
  testId,
}: {
  defaults: SettingsDocument;
  entry: Applied;
  before: Applied | undefined;
  testId: string;
}) {
  const t = useT();
  if (entry.previous !== null && before === undefined)
    return (
      <span data-testid={testId} style={MUTED}>
        {t("modelSettings.versionChangesUnknown")}
      </span>
    );
  const changes = diffEffective((defaults ?? {}) as Patch, before?.patch ?? {}, entry.patch);
  if (changes.length === 0)
    return (
      <span data-testid={testId} style={MUTED}>
        {/* The first version has no version before it: it is compared with the
            deploy defaults, and says so (final review 12). */}
        {t(entry.previous === null ? "modelSettings.versionChangesNoneFirst" : "modelSettings.versionChangesNone")}
      </span>
    );
  return <ChangeList changes={changes} testId={testId} />;
}

function KeysTab({
  client,
  keys,
  applied,
  draft,
  goEdit,
}: {
  client: Client;
  keys: Record<string, string>;
  applied: string[];
  draft: Draft;
  goEdit: () => void;
}) {
  const t = useT();
  const dialog = useDialog();
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const [value, setValue] = useState("");
  const [refused, setRefused] = useState<string[]>([]);
  const done = () => {
    setRefused([]);
    draft.clearNotice();
    void queryClient.invalidateQueries({ queryKey: qk.modelSettings });
  };
  const set = useMutation({
    meta: { silentError: true },
    mutationFn: () => client.setKey(path.trim(), value),
    // An earlier result (an Apply's "Applied: version …") is not this action's:
    // left beside a refusal it read as the key applied (final review 11).
    onMutate: () => draft.clearNotice(),
    onSuccess: () => {
      setPath("");
      setValue("");
      done();
    },
    onError: (e) => setRefused(problemsOf(e).length ? problemsOf(e) : [messageOf(e)]),
  });
  // A failed Clear is said on the key's own row, not at the path field — that
  // field was not what failed (final review 6; NN/g "Reporting Errors in Forms").
  const [clearFailed, setClearFailed] = useState<{ path: string; error: string } | null>(null);
  const clear = useMutation({
    meta: { silentError: true },
    mutationFn: (p: string) => client.clearKey(p),
    onMutate: () => setClearFailed(null),
    onSuccess: done,
    onError: (e, p) =>
      setClearFailed({ path: p, error: problemsOf(e).length ? problemsOf(e).join(" · ") : messageOf(e) }),
  });

  const askToClear = async (p: string) => {
    const choice = await dialog.confirm({
      title: t("modelSettings.keyClearTitle", { path: p }),
      // A key not in force now: clearing it only withdraws a staged change.
      body: t(applied.includes(p) ? "modelSettings.keyClearBody" : "modelSettings.keyClearUnappliedBody"),
      actions: [
        { id: "cancel", label: t("modelSettings.cancel") },
        { id: "clear", label: t("modelSettings.keyClear"), variant: "danger" },
      ],
    });
    if (choice === "clear") clear.mutate(p);
  };

  // Every key the page knows: set ones, and ones cleared but still in force
  // until the next Apply (review r2: a cleared key vanished from the list
  // while it went on running).
  const pending = new Set(draft.pendingKeys);
  const rows = [...new Set([...Object.keys(keys), ...draft.pendingKeys])].sort();

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <p style={{ ...MUTED, margin: 0 }}>{t("modelSettings.keysDesc")}</p>
      {rows.length === 0 ? (
        <p data-testid="model-settings-keys-empty" style={{ ...MUTED, margin: 0 }}>
          {t("modelSettings.keysNone")}
        </p>
      ) : (
        <ul
          data-testid="model-settings-keys"
          style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "grid", gap: 6 }}
        >
          {rows.map((p) => {
            const isSet = p in keys;
            return (
              <li key={p} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ ...MONO, overflowWrap: "anywhere" }}>{p}</span>
                {isSet && <span style={{ ...MONO, ...MUTED }}>{keys[p] || t("modelSettings.keyIsSet")}</span>}
                {pending.has(p) && (
                  <span data-testid={`model-settings-key-pending-${p}`} style={BADGE}>
                    {t(isSet ? "modelSettings.keyPendingSet" : "modelSettings.keyPendingCleared")}
                  </span>
                )}
                {isSet && (
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
                )}
                {clearFailed?.path === p && (
                  <span
                    role="alert"
                    data-testid={`model-settings-key-clear-failed-${p}`}
                    style={{ ...MUTED, ...WRAP, color: "var(--err)" }}
                  >
                    {clearFailed.error}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {draft.pendingKeys.length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={STATUS}>{t("modelSettings.keysWaiting", { n: String(draft.pendingKeys.length) })}</span>
          <ApplyButton draft={draft} testId="model-settings-keys-apply" explain where="keys" goEdit={goEdit} />
        </div>
      )}
      {/* An Apply empties the pending row it sits in; its result stays said here
          (NN/g heuristic #1 — feedback where the person acted). */}
      {draft.pendingKeys.length === 0 && draft.notice && (
        <p role="status" data-testid="model-settings-notice" style={{ ...STATUS, margin: 0 }}>
          {draft.notice}
        </p>
      )}
      {/* Visible labels, not placeholders — NN/g "Placeholders in Form Fields
          Are Harmful": a placeholder disappears as you type and was cut off. */}
      {/* The format hint sits under its own field: hints are "persistent and
          placed outside of the field" (NN/g "Placeholders in Form Fields Are
          Harmful"), and beside the field they explain, not the row (NN/g
          "Proximity"). So the row aligns to the inputs' tops and the button
          gets a label-high spacer. */}
      {/* A form, so Enter sets the key as it does in any form. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (path.trim() && value && !set.isPending) set.mutate();
        }}
        style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}
      >
        <div data-testid="model-settings-key-path-field" style={{ display: "grid", gap: 4, flex: "1 1 20rem", minWidth: 0 }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={LABEL}>{t("modelSettings.keyPathLabel")}</span>
          <input
            className="input"
            data-testid="model-settings-key-path"
            aria-invalid={refused.length > 0}
            aria-describedby={
              refused.length > 0
                ? "model-settings-key-path-help model-settings-key-refused"
                : "model-settings-key-path-help"
            }
            value={path}
            onChange={(e) => setPath(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            style={MONO}
          />
          </label>
          {/* Beside the label, not inside it: inside, it would become part of
              the field's name instead of its description. */}
          <span id="model-settings-key-path-help" style={{ ...MUTED, overflowWrap: "anywhere" }}>
            {t("modelSettings.keyPath")}
          </span>
          {/* An error, drawn as the other tabs draw theirs and under the field every refusal names
              (NN/g "10 Design Guidelines for Reporting Errors in Forms"). */}
          {refused.length > 0 && (
            <p
              role="alert"
              id="model-settings-key-refused"
              data-testid="model-settings-key-refused"
              className="ev-banner ev-banner--err"
              style={{ ...WRAP, margin: 0 }}
            >
              {refused.join(" · ")}
            </p>
          )}
        </div>
        <label style={{ display: "grid", gap: 4, flex: "1 1 12rem", minWidth: 0 }}>
          <span style={LABEL}>{t("modelSettings.keyValue")}</span>
          <input
            className="input"
            data-masked
            data-testid="model-settings-key-value"
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
        </label>
        <div style={{ display: "grid", gap: 4 }}>
          <span aria-hidden="true" style={LABEL}>
            {"\u00a0"}
          </span>
          <button
            type="submit"
            className="btn"
            data-variant="secondary"
            data-testid="model-settings-key-set"
            disabled={!path.trim() || !value || set.isPending}
          >
            {t("modelSettings.keySet")}
          </button>
        </div>
      </form>
      {(!path.trim() || !value) && (
        <p data-testid="model-settings-key-why" style={{ ...MUTED, margin: 0 }}>
          {t("modelSettings.keyNeedBoth")}
        </p>
      )}
    </div>
  );
}
