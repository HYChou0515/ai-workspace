/**
 * The "請幫我查" card (docs/plan-outside-lookup.md): the backend may not reach
 * the internet, the person's browser can, and the AI asked them to look
 * something up.
 *
 * Two kinds (D2, D5): a SEARCH, with one button per destination the deploy
 * configured (`server.lookup_targets`) and the query editable first (D8); or a
 * PAGE, whose whole address is shown before the one button that opens it.
 * Either way the person brings back what they found — pasted (a web page
 * arrives as Markdown, links and tables kept), attached, with its source — or
 * says they could not find it (D7). One request saves it under `lookups/`
 * (when they may add files) and sends the message that answers this call.
 *
 * Guidance (cited in the plan):
 * - Opening a new tab is announced before the click, in the button's name and a
 *   line of text, and carries the ↗ cue (NN/g, "Opening Links in New Browser
 *   Windows and Tabs") — the chat is the person's launching point.
 * - Button emphasis follows Material 3: the one filled button completes the
 *   flow (送出); the outlined ones are the important-but-not-final actions (the
 *   searches, 開啟這個網址); the text ones are the least (複製, 查不到).
 * - The attachment control follows GOV.UK's file upload: a visible label, a
 *   secondary "choose" button, a drop zone that stays visible, "no file chosen"
 *   until one is, then the names — each removable.
 *
 * Pressing 送出 takes only the actions away; what was sent stays, read-only, as
 * the record (the `ask_user` card's rule). Outside an item chat (the knowledge
 * base, a replay) the request shows without actions.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { HttpError } from "../api/http";
import {
  type OutsideAnswer,
  type OutsideLookupClient,
  outsideLookupApi,
  searchUrl,
} from "../api/outsideLookup";
import { qk } from "../api/queryKeys";
import { useChatItem } from "../hooks/chatItem";
import { CHAT_QUOTA_KEY } from "../hooks/useChatSession";
import { htmlToMarkdown } from "../lib/htmlToMarkdown";
import { useT } from "../lib/i18n";
import { quotaMessage } from "../lib/quotaFailure";
import type { OutsideLookup } from "../renderers/outsideLookup";

const NEW_TAB = "noopener,noreferrer";

/** Today in the person's own calendar, `YYYY-MM-DD` — what the saved file is
 * named by. The server's clock is UTC; a lookup at 07:00 in UTC+8 would
 * otherwise be filed under yesterday. */
function localDate(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function OutsideLookupCard({
  callId,
  lookup,
  client = outsideLookupApi,
}: {
  /** The `ask_outside` call — what the answer says it answers. */
  callId: string;
  lookup: OutsideLookup;
  client?: OutsideLookupClient;
}) {
  const t = useT();
  const item = useChatItem();
  const ids = useId();
  const isSearch = "query" in lookup;
  const [query, setQuery] = useState(isSearch ? lookup.query : "");
  const [content, setContent] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [target, setTarget] = useState("");
  const [copied, setCopied] = useState(false);
  const [giveUp, setGiveUp] = useState(false);
  const [reason, setReason] = useState("");
  // What the card itself says is missing, before anything is sent.
  const [hint, setHint] = useState<string | null>(null);
  // Pressed and accepted — the transcript's `answered` is a round trip away.
  const [done, setDone] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const targets = useQuery({
    queryKey: qk.lookupTargets(),
    queryFn: () => client.targets(),
    enabled: Boolean(item) && isSearch,
    staleTime: Infinity,
  });
  const answer = useMutation({
    mutationFn: (a: OutsideAnswer) => {
      if (!item) throw new Error("no chat to answer in");
      return client.answer({
        slug: item.slug,
        itemId: item.itemId,
        chatId: item.chatId,
        callId,
        answer: a,
        query: isSearch ? query.trim() : undefined,
        date: localDate(),
      });
    },
    onMutate: () => setHint(null),
    onSuccess: (saved, a) =>
      setDone(
        a.kind === "not_found"
          ? t("lookup.sentNotFound")
          : saved.path
            ? t("lookup.savedTo", { path: saved.path })
            : t("lookup.sent"),
      ),
  });

  const head = (
    <>
      <p className="outside-lookup-title">{t("lookup.title")}</p>
      <p className="outside-lookup-why">{lookup.why}</p>
    </>
  );
  if (!item) {
    return (
      <div className="outside-lookup-card" data-testid="outside-lookup-card">
        {head}
        <p className="outside-lookup-subject">{isSearch ? lookup.query : lookup.url}</p>
      </div>
    );
  }
  if (!done && item.answered?.(callId)) {
    return (
      <div className="outside-lookup-card" data-testid="outside-lookup-card">
        {head}
        <p className="outside-lookup-subject">{isSearch ? lookup.query : lookup.url}</p>
        <p className="outside-lookup-note">{t("lookup.answered")}</p>
      </div>
    );
  }
  const locked = done !== null;
  const busy = answer.isPending;
  // Without `add_content` the server saves nothing and refuses attachments.
  const canAttach = item.canAddFiles !== false;
  const error = hint ?? (answer.error ? failure(t, answer.error) : null);

  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text).then(() => setCopied(true));
  };
  const addFiles = (list: FileList | null) => {
    if (list?.length) setFiles((f) => [...f, ...Array.from(list)]);
  };
  const submitFound = () => {
    if (!content.trim() && files.length === 0) {
      setHint(t(canAttach ? "lookup.empty" : "lookup.emptyText"));
      return;
    }
    answer.mutate({
      kind: "found",
      content,
      sourceUrl: sourceUrl.trim(),
      target,
      attachments: files,
    });
  };

  return (
    <div className="outside-lookup-card" data-testid="outside-lookup-card">
      {head}

      {isSearch ? (
        <div className="outside-lookup-section">
          <label className="outside-lookup-label" htmlFor={`${ids}-q`}>
            {t("lookup.query")}
          </label>
          <input
            id={`${ids}-q`}
            className="input"
            value={query}
            readOnly={locked}
            onChange={(e) => {
              setQuery(e.target.value);
              setCopied(false);
            }}
          />
          <div className="outside-lookup-actions">
            {(targets.data ?? []).map((tg) => (
              <button
                key={tg.name}
                type="button"
                className="btn"
                data-size="sm"
                data-variant="secondary"
                aria-label={t("lookup.target", { name: tg.name })}
                disabled={!query.trim()}
                onClick={() => {
                  setTarget(tg.name);
                  window.open(searchUrl(tg, query.trim()), "_blank", NEW_TAB);
                }}
              >
                {tg.name} <span aria-hidden="true">↗</span>
              </button>
            ))}
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="ghost"
              onClick={() => copy(query)}
            >
              {copied ? t("lookup.copied") : t("lookup.copy")}
            </button>
          </div>
          <p className="outside-lookup-hint">{t("lookup.newTab")}</p>
        </div>
      ) : (
        <div className="outside-lookup-section">
          <span className="outside-lookup-label">{t("lookup.url")}</span>
          <code className="outside-lookup-url">{lookup.url}</code>
          <div className="outside-lookup-actions">
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="secondary"
              aria-label={t("lookup.openUrlLabel")}
              onClick={() => window.open(lookup.url, "_blank", NEW_TAB)}
            >
              {t("lookup.openUrl")} <span aria-hidden="true">↗</span>
            </button>
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="ghost"
              onClick={() => copy(lookup.url)}
            >
              {copied ? t("lookup.copied") : t("lookup.copyUrl")}
            </button>
          </div>
          <p className="outside-lookup-hint">{t("lookup.newTab")}</p>
        </div>
      )}

      {giveUp ? (
        <div className="outside-lookup-section">
          <label className="outside-lookup-label" htmlFor={`${ids}-r`}>
            {t("lookup.reason")}
          </label>
          <input
            id={`${ids}-r`}
            className="input"
            value={reason}
            readOnly={locked}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
      ) : (
        <>
          <div className="outside-lookup-section">
            <label className="outside-lookup-label" htmlFor={`${ids}-c`}>
              {t("lookup.found")}
            </label>
            <p className="outside-lookup-hint">{t("lookup.foundHint")}</p>
            <textarea
              id={`${ids}-c`}
              className="input outside-lookup-paste"
              rows={6}
              value={content}
              readOnly={locked}
              onChange={(e) => setContent(e.target.value)}
              onPaste={(e) => {
                const html = e.clipboardData.getData("text/html");
                if (!html || locked) return; // plain text: the browser's own paste
                e.preventDefault();
                const md = htmlToMarkdown(html);
                const box = e.currentTarget;
                const start = box.selectionStart ?? content.length;
                const end = box.selectionEnd ?? content.length;
                setContent(content.slice(0, start) + md + content.slice(end));
              }}
            />
          </div>

          {!canAttach ? (
            <p className="outside-lookup-hint">{t("lookup.textOnly")}</p>
          ) : (
            <div className="outside-lookup-section">
              <label className="outside-lookup-label" htmlFor={`${ids}-f`}>
                {t("lookup.attach")}
              </label>
              <input
                id={`${ids}-f`}
                ref={fileInput}
                type="file"
                multiple
                className="outside-lookup-file-input"
                disabled={locked}
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              {!locked && (
                <div
                  className="outside-lookup-drop"
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    addFiles(e.dataTransfer.files);
                  }}
                >
                  <button
                    type="button"
                    className="btn"
                    data-size="sm"
                    data-variant="secondary"
                    onClick={() => fileInput.current?.click()}
                  >
                    {t("lookup.choose")}
                  </button>
                  <span className="outside-lookup-hint">{t("lookup.drop")}</span>
                </div>
              )}
              {files.length === 0 ? (
                <p className="outside-lookup-hint">{t("lookup.noFile")}</p>
              ) : (
                <ul className="outside-lookup-files">
                  {files.map((f, i) => (
                    <li key={`${f.name}-${i}`}>
                      <span>{f.name}</span>
                      {!locked && (
                        <button
                          type="button"
                          className="btn"
                          data-size="sm"
                          data-variant="ghost"
                          aria-label={t("lookup.remove", { name: f.name })}
                          onClick={() => setFiles((all) => all.filter((_f, j) => j !== i))}
                        >
                          ✕
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="outside-lookup-section">
            <label className="outside-lookup-label" htmlFor={`${ids}-s`}>
              {t("lookup.source")}
            </label>
            <input
              id={`${ids}-s`}
              className="input"
              type="url"
              value={sourceUrl}
              readOnly={locked}
              onChange={(e) => setSourceUrl(e.target.value)}
            />
          </div>
        </>
      )}

      {error && (
        <p className="outside-lookup-error" role="alert">
          {error}
        </p>
      )}

      {locked ? (
        <p className="outside-lookup-note">{done}</p>
      ) : giveUp ? (
        <div className="outside-lookup-actions">
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="primary"
            disabled={busy}
            onClick={() => answer.mutate({ kind: "not_found", reason: reason.trim() })}
          >
            {t("lookup.sendNotFound")}
          </button>
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="ghost"
            disabled={busy}
            onClick={() => {
              setGiveUp(false);
              setHint(null);
              answer.reset();
            }}
          >
            {t("lookup.back")}
          </button>
        </div>
      ) : (
        <div className="outside-lookup-actions">
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="primary"
            disabled={busy}
            onClick={submitFound}
          >
            {t("lookup.send")}
          </button>
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="ghost"
            disabled={busy}
            onClick={() => {
              setGiveUp(true);
              setHint(null);
              answer.reset();
            }}
          >
            {t("lookup.notFound")}
          </button>
        </div>
      )}
    </div>
  );
}

/** What a refused answer says: a full workspace the way a refused chat send
 * says it (the same keys), otherwise the server's own reason. */
function failure(t: ReturnType<typeof useT>, err: unknown): string {
  const status = err instanceof HttpError ? err.status : undefined;
  const quota = quotaMessage(t, CHAT_QUOTA_KEY, {
    ...(err as { code?: string; also?: string[] } | null),
    status,
  });
  if (quota) return quota;
  return t("lookup.failed", { reason: err instanceof Error ? err.message : String(err) });
}
