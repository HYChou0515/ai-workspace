/**
 * The per-item environment variables panel.
 *
 * The item's `env_vars` are handed to the tools its agent runs — API keys and
 * the like. This edits them; the backend names them on the `exec` that
 * dispatches each tool, per turn, and stores them nowhere else (#673).
 *
 * Shown only to someone who may actually store them (`write_meta`) — the shell
 * withholds the save callback otherwise, and the header draws no button without
 * it. Before that, every Participant was offered this panel and answered 403 by
 * a request no one was reading.
 *
 * A text box holding the whole set as `.env` text, and — for the variables the
 * item's own tools DECLARED (#750) — a field apiece above it. The box came
 * first and stays first for a reason: what people actually do with these is
 * paste a block in from somewhere else (a colleague, a password manager,
 * another project's `.env`), and a pure row editor turns that into one Add plus
 * two clicks per line. It also makes the format the same one they already have
 * on their disk, so Import is a convenience rather than the only way in.
 *
 * The fields do not replace that; they answer a different question. The box is
 * how you enter values you already have. The fields are how you find out WHICH
 * values this workspace's tools are waiting for — which, before #750, nothing
 * anywhere could tell you.
 *
 * Both edit ONE value. The fields read and write the box's text (through
 * `setEnvValue`, in place, so a keystroke in a field cannot cost the reader the
 * comments they wrote), and Save stores what the box parses to. Nothing here
 * holds a second copy of anything.
 *
 * Storage is unchanged: the text is parsed into `dict[str, str]` on save
 * (`lib/envFile.ts`). Two consequences worth knowing rather than hiding:
 * comments and blank lines are not stored, so they do not survive a reopen; and
 * a name written twice keeps the last, which is dotenv's own rule and is at
 * least visible here, both lines being on screen.
 *
 * Nothing is masked, deliberately — but the reason has changed and the old one
 * is worth not repeating. It used to be "the agent can read the delivered file
 * anyway"; there is no file any more (#673). The reason now is that the values
 * are a plain field on the item record, returned unredacted to anyone with
 * `read_meta`, so a mask here hides them from the one person who may edit them
 * and from nobody else — it would remove the ability to spot a mistyped key and
 * deliver no protection at all. If these should ever be secret FROM readers,
 * that is a backend change (redact on read), not a mask in this component.
 */
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api as defaultApi } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient } from "../api/types";
import { mergeEnv, parseEnvText, setEnvValue, toEnvText, unstorable } from "../lib/envFile";
import { deriveEnvNeeds } from "../lib/envNeeds";
import { fuzzyFilter } from "../lib/fuzzy";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { ModalShell } from "./ModalShell";
import { Popover, PopoverItem } from "./Popover";

export function EnvVarsModal({
  envVars,
  onSave,
  onClose,
  slug,
  itemId,
  client = defaultApi,
}: {
  envVars: Record<string, string>;
  onSave: (next: Record<string, string>) => void | Promise<void>;
  onClose: () => void;
  /** When given, the panel also offers a field per variable the item's current
   * tools declared (#750). Optional so the box alone still works — the whole
   * feature is a convenience over an editor that was already complete. */
  slug?: string;
  itemId?: string;
  client?: Pick<ApiClient, "getItemTools" | "getEnvProviders" | "resolveEnvProvider">;
}) {
  const t = useT();
  const [text, setText] = useState(() => toEnvText(envVars));
  const fileRef = useRef<HTMLInputElement>(null);

  // The picker's own query, by the same key: one answer to "which tools does
  // this item run", shared with the tool picker rather than re-resolved here.
  const toolsQ = useQuery({
    queryKey: qk.itemTools(slug ?? "", itemId ?? ""),
    queryFn: () => client.getItemTools(slug!, itemId!),
    enabled: Boolean(slug && itemId),
  });

  // Derived from the TEXT BOX, not from the last saved state: the form and the
  // box edit one value, and a field disagreeing with the text above it would
  // leave the person to guess which one Save is going to use.
  const values = parseEnvText(text);
  const view = deriveEnvNeeds(toolsQ.data ?? [], values);

  // In place, not a round trip through the map: this runs on every keystroke
  // in a declared field, and rebuilding the text from the parsed map would
  // silently delete the reader's comments and any line still being typed.
  const setVar = (name: string, value: string) => setText(setEnvValue(text, name, value));

  // Which tool's variables are on screen. A FILTER over one set of values, not
  // a form per tool: a variable two tools want is one stored name with one
  // value, and per-tab copies would let it read differently depending on where
  // you looked. Falls back to the first group so a tab for a tool switched off
  // between renders cannot leave the panel showing nothing.
  // What this deploy can obtain from something a person types. Empty is the
  // ordinary case, and the panel is complete without it — the button only ever
  // saves typing.
  const providersQ = useQuery({
    queryKey: qk.envProviders(slug ?? "", itemId ?? ""),
    queryFn: () => client.getEnvProviders(slug!, itemId!),
    enabled: Boolean(slug && itemId),
  });

  // A provider is offered when it produces a name some tool asked for. That
  // name is the ONLY join: the tool never named the provider, so a third-party
  // author cannot choose which credential this dialog asks for.
  const declaredNames = new Set(view.groups.flatMap((g) => g.fields.map((f) => f.name)));
  const offered = (providersQ.data ?? []).filter((p) =>
    p.produces.some((name) => declaredNames.has(name)),
  );

  const [dialog, setDialog] = useState<string | null>(null);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [credError, setCredError] = useState<string | null>(null);
  const [exchanging, setExchanging] = useState(false);
  const openProvider = offered.find((p) => p.id === dialog);

  /** Run the exchange and put the result in the FORM. Nothing is stored: the
   * person still presses Save, the same as after an Import. */
  const runExchange = async () => {
    if (!openProvider) return;
    setExchanging(true);
    setCredError(null);
    try {
      const env = await client.resolveEnvProvider(slug!, itemId!, openProvider.id, creds);
      // Refused WHOLE and by name when a pair cannot survive this panel's text
      // format: being told the value does not fit is recoverable, being handed
      // a truncated certificate is not, and applying only the pairs that
      // happened to fit leaves a half-exchange nobody asked for.
      //
      // `unstorable` asks by round trip rather than by forbidden characters.
      // The first version of this check tested for newlines, which was the case
      // in front of me — and let a NAME containing `=` through, which stores a
      // different variable than the one on screen.
      const cannotStore = unstorable(env);
      if (cannotStore.length > 0) {
        setCredError(t("env.providerValueTooComplex", { names: cannotStore.join(", ") }));
        return;
      }
      // Merged, not replaced, and unfiltered: a provider may legitimately
      // return a name no tool declared, and dropping it would discard exactly
      // what an incomplete declaration most needs to keep. Applied name by name
      // through the same in-place writer the fields use, so a login does not
      // cost someone the comments they had written either.
      setText((prev) =>
        Object.entries(env).reduce((acc, [name, v]) => setEnvValue(acc, name, v), prev),
      );
      setDialog(null);
      setCreds({});
    } catch (err) {
      // The implementation's own sentence, when it sent one — it is the only
      // party that knows why its login said no, and "帳號或密碼不正確" is what
      // the person needs. Falling back to a generic line rather than to
      // `err.message`, which is a status line with a JSON envelope stapled to
      // it: internals, in front of someone who was only trying to log in.
      const why = (err as { detail?: { why?: unknown } })?.detail?.why;
      setCredError(typeof why === "string" && why ? why : t("env.providerFailed"));
    } finally {
      setExchanging(false);
    }
  };

  const [tab, setTab] = useState<string | null>(null);
  const [toolQuery, setToolQuery] = useState("");
  const shownGroup = view.groups.find((g) => g.key === tab) ??
    view.groups[0] ?? { key: "", label: "", fields: [], author: null, version: null, missing: 0 };
  // Narrowed on what someone would type: the tool's name or the publisher's.
  const offeredGroups = fuzzyFilter(
    toolQuery,
    view.groups,
    (g) => `${g.label} ${g.author ?? ""}`,
  );

  /** Import MERGES into what is in the BOX, not into the last saved state: the
   * box is what the user is looking at, and importing on top of something they
   * cannot see would be a different operation than it appears to be. */
  const importFile = async (file: File) => {
    setText(toEnvText(mergeEnv(parseEnvText(text), parseEnvText(await file.text()))));
  };

  /** Export what is in the box, unsaved edits included — a download that
   * silently disagreed with the panel would be worse than none. Straight to the
   * browser, never into the workspace: a file there is one the agent can read. */
  const exportFile = () => {
    const url = URL.createObjectURL(
      new Blob([toEnvText(parseEnvText(text))], { type: "text/plain" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = ".env";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={t("env.title")}
      data-testid="env-modal"
      width={520}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{t("env.title")}</strong>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("env.desc")}
      </p>

      {(view.groups.length > 0 || view.undeclared.length > 0) && (
        <p
          data-testid="env-missing"
          style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}
        >
          {view.missingRequired.length > 0
            ? t("env.stillMissing", { names: view.missingRequired.join(", ") })
            : t("env.nothingMissing")}
        </p>
      )}

      {view.groups.length > 1 && (
        <Popover
          width={320}
          trigger={({ onClick, open }) => (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="env-tool-trigger"
              aria-expanded={open}
              onClick={onClick}
              style={{ justifyContent: "space-between", width: "100%" }}
            >
              {/* Collapsed, and naming what is showing. The panel already
                  carries an intro, a summary, the fields, a login button, a
                  caveat and the box; an always-open list pushes all of it down
                  for something most visits never touch. */}
              <span>{shownGroup.label}</span>
              {/* A glyph, not `<Icon name="chevron-down">`: this set has no
                  chevron, and an unknown name renders NOTHING — a trigger with
                  no affordance, and no error anywhere to say why. */}
              <span aria-hidden style={{ color: "var(--text-paper-d)", fontSize: pxToRem(10) }}>
                ▾
              </span>
            </button>
          )}
        >
          {(close) => (
            <div style={{ display: "grid", gap: 4 }}>
              <input
                type="search"
                className="kb-input"
                data-testid="env-tool-search"
                value={toolQuery}
                onChange={(e) => setToolQuery(e.target.value)}
                placeholder={t("env.searchTools")}
                aria-label={t("env.searchTools")}
                style={{ width: "100%" }}
              />
              {/* Capped and scrollable, like every other list of this shape
                  here: the search box stays outside the scroll area so it can
                  never be scrolled out of reach. */}
              <div style={{ maxHeight: "min(240px, 30vh)", overflowY: "auto" }}>
                {offeredGroups.map((group) => (
                  <PopoverItem
                    key={group.key}
                    testId={`env-tool-${group.key}`}
                    selected={group.key === shownGroup.key}
                    onClick={() => {
                      setTab(group.key);
                      close();
                    }}
                  >
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ fontWeight: 500 }}>{group.label}</span>
                      {/* Who shipped it and which release resolved (#724): two
                          bundles can share a name and differ only in this. */}
                      {(group.author || group.version) && (
                        <span
                          style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}
                        >
                          {" "}
                          {[group.author, group.version].filter(Boolean).join(" · ")}
                        </span>
                      )}
                    </span>
                    <span
                      style={{
                        // Pushed right explicitly rather than left to the
                        // sibling's `flex: 1`, which did not expand inside the
                        // shared item and left the count touching the name.
                        marginLeft: "auto",
                        whiteSpace: "nowrap",
                        fontSize: pxToRem(11),
                        color: "var(--text-paper-d)",
                      }}
                    >
                      {group.missing > 0
                        ? t("env.toolStillNeeds", { count: String(group.missing) })
                        : t("env.toolReady")}
                    </span>
                  </PopoverItem>
                ))}
                {offeredGroups.length === 0 && (
                  <p
                    data-testid="env-tool-none"
                    style={{
                      margin: 0,
                      padding: "6px 10px",
                      fontSize: pxToRem(12),
                      color: "var(--text-paper-d)",
                    }}
                  >
                    {t("env.noToolMatches")}
                  </p>
                )}
              </div>
            </div>
          )}
        </Popover>
      )}

      {view.groups.length > 0 && (
        <section data-testid={`env-group-${shownGroup.key}`}>
          <strong style={{ fontSize: pxToRem(12) }}>{shownGroup.label}</strong>
          {shownGroup.fields.map((field) => (
            <label key={field.name} style={{ display: "block", marginTop: 8 }}>
              <span
                style={{
                  fontFamily: "var(--font-mono, ui-monospace, monospace)",
                  fontSize: pxToRem(12),
                }}
              >
                {field.name}
              </span>
              {field.description && (
                <span
                  style={{
                    display: "block",
                    fontSize: pxToRem(11),
                    color: "var(--text-paper-d)",
                  }}
                >
                  {field.description}
                </span>
              )}
              {field.wantedBy.length > 1 && (
                <span
                  data-testid={`env-shared-${field.name}`}
                  style={{ display: "block", fontSize: pxToRem(11), color: "var(--text-paper-d)" }}
                >
                  {t("env.alsoUsedBy", { tools: field.wantedBy.join(", ") })}
                </span>
              )}
              <input
                data-testid={`env-field-${field.name}`}
                value={values[field.name] ?? ""}
                onChange={(e) => setVar(field.name, e.target.value)}
                spellCheck={false}
                // Never `true`. A required-but-empty field is NOT YET FILLED,
                // not wrong: the declaration is a hint and Save is always
                // available, so an error style would be a gate in disguise —
                // the button works, the screen says it should not be pressed.
                aria-invalid="false"
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  padding: "6px 8px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: 6,
                  background: "var(--paper)",
                  color: "var(--text-paper)",
                  fontFamily: "var(--font-mono, ui-monospace, monospace)",
                  fontSize: pxToRem(12),
                }}
              />
            </label>
          ))}
        </section>
      )}

      {offered.map((provider) => (
        <div key={provider.id}>
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid={`env-provider-${provider.id}`}
            onClick={() => {
              setDialog(provider.id);
              setCreds({});
              setCredError(null);
            }}
          >
            {provider.label}
          </button>
          {/* Which variables it will fill, next to the button. Two systems can
              look alike; a person about to type a production password needs to
              see what they are about to do BEFORE typing it, not after. */}
          <span style={{ marginLeft: 8, fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
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
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  padding: "6px 8px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: 6,
                  background: "var(--paper)",
                  color: "var(--text-paper)",
                }}
              />
            </label>
          ))}
          {credError && (
            <p
              data-testid="env-cred-error"
              style={{ margin: 0, fontSize: pxToRem(11), color: "var(--err)" }}
            >
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

      {view.undeclared.length > 0 && (
        <p
          data-testid="env-undeclared"
          style={{ margin: 0, fontSize: pxToRem(11), color: "var(--text-paper-d)" }}
        >
          {t("env.undeclared", { tools: view.undeclared.join(", ") })}
        </p>
      )}

      <textarea
        data-testid="env-text"
        aria-label={t("env.title")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"FOO=BAR\nBAZ=HOO"}
        spellCheck={false}
        rows={10}
        style={{
          width: "100%",
          boxSizing: "border-box",
          padding: "8px 10px",
          border: "1px solid var(--paper-3)",
          borderRadius: 6,
          background: "var(--paper)",
          color: "var(--text-paper)",
          // Monospace: these are keys, and a column of them is proofread by
          // eye. Proportional type hides the difference between l/1 and O/0.
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: pxToRem(12),
          lineHeight: 1.6,
          resize: "vertical",
          whiteSpace: "pre",
          overflowWrap: "normal",
          overflowX: "auto",
        }}
      />

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
        <input
          ref={fileRef}
          type="file"
          accept=".env,text/plain"
          data-testid="env-import"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void importFile(f);
            e.target.value = ""; // so re-picking the same file fires again
          }}
        />
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-import-button"
          onClick={() => fileRef.current?.click()}
        >
          {t("env.import")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-export"
          style={{ marginRight: "auto" }}
          onClick={exportFile}
        >
          {t("env.export")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-cancel"
          onClick={onClose}
        >
          {t("env.cancel")}
        </button>
        <button type="button" className="btn" data-size="sm" data-testid="env-save" onClick={() => void onSave(parseEnvText(text))}>
          {t("env.save")}
        </button>
      </div>
    </ModalShell>
  );
}
