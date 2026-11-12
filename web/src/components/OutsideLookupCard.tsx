/**
 * The "請幫我查" card (docs/plan-outside-lookup.md): the backend may not reach
 * the internet, the person's browser can, and the AI asked them to look
 * something up.
 *
 * Two kinds (D2, D5): a SEARCH, with one button per destination the deploy
 * configured (`server.lookup_targets`), an empty box the person types or
 * pastes the keywords into and the AI's query beside it as a suggestion to copy
 * (D13); or a
 * PAGE, whose whole address is shown before the one button that opens it.
 *
 * Either way the person brings back what they found — pasted (a web page
 * arrives as Markdown, links and tables kept), attached, with its source — or
 * says they could not find it (D7). One request saves it under `lookups/`
 * (when they may add files) and sends the message that answers this call.
 *
 * Which target a search goes to is the person's (docs/plan-lookup-target-choice.md
 * T1): with their helper, a radio group of the deploy's targets — each with what
 * it searches (T4), the first chosen to begin with, never the AI's (T2), whose
 * suggestion is marked beside its option. By hand, one new-tab button per target
 * — none for a target with `preview: false` (T5).
 *
 * Guidance (cited in the plan):
 * - Choosing the target is choosing one of a few: radio buttons rather than a
 *   drop-down, one chosen by default (NN/g, "Checkboxes vs. Radio Buttons";
 *   "Radio Buttons: Select One by Default or Leave All Unselected?").
 * - Opening a new tab is announced before the click, in the button's name and a
 *   line of text, and carries the ↗ cue (NN/g, "Opening Links in New Browser
 *   Windows and Tabs") — the chat is the person's launching point.
 * - Button emphasis follows Material 3: the one filled button completes the
 *   flow (送出); the outlined ones are the important-but-not-final actions (the
 *   searches, 開啟這個網址); the text ones are the least (複製建議, 查不到).
 * - The attachment control follows GOV.UK's file upload: a visible label, a
 *   secondary "choose" button, a drop zone that stays visible, "no file chosen"
 *   until one is, then the names — each removable.
 *
 * Pressing 送出 takes only the actions away; what was sent stays, read-only, as
 * the record (the `ask_user` card's rule). Outside an item chat (the knowledge
 * base, a replay) the request shows without actions.
 *
 * With the person's helper connected (docs/plan-outside-helper.md
 * §3.5) the helper fetches instead: a search asks only for the nod (「{name} search」, with
 * the keywords the person typed — D13); a URL an earlier lookup in this chat brought back
 * goes with no nod at all (D12 — the server decides, the card just asks); the
 * card then shows the errand under way, failed or done. Doing it by hand stays
 * one press away (「自己查」). With no helper the card is the manual one above
 * (D7), plus a line saying the helper is not connected and where to get it.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";

import { API_BASE, HttpError } from "../api/http";
import {
  type OutsideAnswer,
  type OutsideLookupClient,
  outsideLookupApi,
  searchUrl,
} from "../api/outsideLookup";
import { qk } from "../api/queryKeys";
import { Icon } from "./Icon";
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
  // plan-outside-lookup D13: the box starts empty — what goes out to search is
  // what the person types or pastes; the AI's query is a suggestion beside it.
  const [query, setQuery] = useState("");
  const [needKeywords, setNeedKeywords] = useState(false);
  /** What the AI asked for, as the record shows it. */
  const subject = "query" in lookup ? lookup.query : lookup.url;
  /** What the box holds for this card: the keywords a search goes out with. */
  const sent = isSearch ? query.trim() : undefined;

  const queryBox = useRef<HTMLInputElement>(null);
  /** `go` with keywords in the box; otherwise say so and put the person there
   * (NN/g: a button that does nothing explains nothing — this one says why). */
  const ifTyped = (go: () => void) => {
    if (!query.trim()) {
      setNeedKeywords(true);
      queryBox.current?.focus();
      return;
    }
    setNeedKeywords(false);
    go();
  };
  const [content, setContent] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [target, setTarget] = useState("");
  // docs/plan-lookup-target-choice.md T1/T2: the target the person chose for
  // their helper — none picked yet reads as the deploy's first.
  const [chosen, setChosen] = useState<string | null>(null);
  // A copy confirms only once the clipboard write resolved, reverts after a
  // moment, and says so when it failed (Primer's copy button).
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (copied !== "copied") return;
    const back = setTimeout(() => setCopied("idle"), 2000);
    return () => clearTimeout(back);
  }, [copied]);
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
        query: sent,
        date: localDate(),
      });
    },
    onMutate: () => setHint(null),
    onSuccess: (saved, a) => {
      setDone(
        a.kind === "not_found"
          ? t("lookup.sentNotFound")
          : saved.path
            ? t("lookup.savedTo", { path: saved.path })
            : t("lookup.sent"),
      );
      // The answer started the AI's next turn, on whichever pod took the POST.
      item?.follow?.(callId);
    },
  });

  // ── the person's helper (docs/plan-outside-helper.md §3.5) ──
  const qc = useQueryClient();
  const answeredAlready = Boolean(item?.answered?.(callId));
  const helperAvailable = Boolean(
    client.helperStatus && client.handOver && client.job && client.cancel,
  );
  const helper = useQuery({
    queryKey: qk.aiWorkspaceHelper(),
    queryFn: () => client.helperStatus!(),
    enabled: Boolean(item) && helperAvailable && !answeredAlready && done === null,
    refetchInterval: 15_000,
  });
  const online = helper.data?.online === true;
  // Whether there is a helper to get at all (§3.7.2): with none published,
  // the card does not point at one (D7).
  const release = useQuery({
    queryKey: qk.aiWorkspaceHelperRelease(),
    queryFn: () => client.release!(),
    enabled: Boolean(item) && Boolean(client.release) && !answeredAlready && done === null,
    staleTime: 10 * 60_000,
  });
  const cardRef = item
    ? { slug: item.slug, itemId: item.itemId, chatId: item.chatId, callId }
    : null;
  const [needsNod, setNeedsNod] = useState(false);
  const [byHand, setByHand] = useState(false);
  const jobKey = qk.outsideJob(item?.itemId ?? "", item?.chatId, callId);
  const job = useQuery({
    queryKey: jobKey,
    queryFn: () => client.job!(cardRef!),
    // Read whether or not this page handed it over: after a reload the card
    // must see a job already under way, or its button would restart it.
    enabled: cardRef !== null && helperAvailable && !answeredAlready && done === null,
    // Polled only while the helper is at it; an ended job does not change.
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === "queued" || s === "claimed" ? 2_000 : false;
    },
  });
  const hand = useMutation({
    mutationFn: (auto: boolean) =>
      client.handOver!({ ...cardRef!, query: sent, auto, target: isSearch ? searchName : undefined }),
    onSuccess: (j) => {
      setNeedsNod(false);
      qc.setQueryData(jobKey, j);
      void qc.invalidateQueries({ queryKey: jobKey }); // and read back how it goes
    },
    // Said in the card, not app-wide: a refused nod shows its reason here
    // (`handError`), and an automatic request refused is just the way back to
    // the nod — not a failed save to announce over the chat.
    meta: { silentError: true },
    // An automatic request refused (a URL no lookup brought back — D12) waits
    // for the nod instead.
    onError: (_err, auto) => {
      if (auto) setNeedsNod(true);
    },
  });
  const stop = useMutation({
    mutationFn: () => client.cancel!(cardRef!),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: jobKey });
    },
  });
  // A page asks on its own, once: the server sends it only if an earlier lookup
  // in this chat brought that link back (D12), and answers 409 otherwise.
  const asked = useRef(false);
  useEffect(() => {
    if (isSearch || !online || answeredAlready || asked.current || !cardRef) return;
    asked.current = true;
    hand.mutate(true);
    // `hand` and `cardRef` are new each render; `asked` makes this run once.
  }, [isSearch, online, answeredAlready]);

  // The helper's answer went in from the helper's own browser and started the
  // AI's next turn there: this page may hear nothing of it (another pod's
  // stream) — so the chat follows it, once (the person's report:
  // 「要重新整理才看得到回覆」). Above every early return: followed, the chat
  // draws the answer, and the card then reads as answered while still here.
  // Once: a job that is done stays done (its polling stops there).
  const followedJob = job.data?.state;
  useEffect(() => {
    if (followedJob === "done") item?.follow?.(callId);
    // `item` is new each render; the job's state is what changes.
  }, [followedJob]);

  // NN/g, "Progress Indicators Make a Slow System Less Insufferable": past ten
  // seconds, say how it is going — the seconds waited are that update.
  // Counted from when this page saw it start.
  const jobState = job.data?.state;
  const working = hand.isPending || jobState === "queued" || jobState === "claimed";
  const [since, setSince] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!working) {
      setSince(null);
      return;
    }
    setSince((s) => s ?? Date.now());
    setNow(Date.now());
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [working]);
  const waited = since === null ? 0 : Math.max(0, Math.round((now - since) / 1000));
  const targetList = targets.data ?? [];
  // T2: what the person chose, or the first — the radio group's default
  const searchName = chosen ?? targetList[0]?.name;
  // T8: the AI's suggestion counts only for a target this deploy lists
  const suggested =
    "query" in lookup && targetList.some((tg) => tg.name === lookup.target)
      ? lookup.target
      : undefined;
  const aiMark = (name: string) =>
    name === suggested ? <span className="outside-lookup-badge">{t("lookup.aiSuggests")}</span> : null;

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
        <p className="outside-lookup-subject">{subject}</p>
      </div>
    );
  }
  if (!done && item.answered?.(callId)) {
    return (
      <div className="outside-lookup-card" data-testid="outside-lookup-card">
        {head}
        <p className="outside-lookup-subject">{subject}</p>
        <p className="outside-lookup-note">{t("lookup.answered")}</p>
      </div>
    );
  }
  const locked = done !== null;
  const busy = answer.isPending;
  // Without `add_content` the server saves nothing and refuses attachments.
  const canAttach = item.canAddFiles !== false;
  const error = hint ?? (answer.error ? failure(t, answer.error) : null);
  // A helper holding this card's job is helping even when its check-ins lapse:
  // it checks in between jobs, and a captcha can keep it on one for minutes.
  const helping = online || jobState === "claimed";
  // By hand: no helper, the person chose to, or the card is already sent.
  const manual = !helping || byHand || locked;
  const handError =
    hand.error && hand.variables === false ? failure(t, hand.error) : null;
  const helperBlock =
    helping && !locked ? (
      <div className="outside-lookup-section" data-testid="outside-lookup-helper">
        {jobState === "done" ? (
          // where it was saved is said by the answer it sent, just below
          <p className="outside-lookup-note">{t("lookup.helperDone")}</p>
        ) : working ? (
          <div className="outside-lookup-actions">
            {job.data?.auto && (
              <p className="outside-lookup-note">{t("lookup.helperFromEarlier")}</p>
            )}
            <p className="outside-lookup-note">
              {/* the live region holds what is happening, not the seconds: a
                  polite region changing every second is read out every second */}
              <span role="status">
                {t(
                  jobState === "claimed" && job.data?.note === "captcha"
                    ? "lookup.helperCaptcha"
                    : isSearch
                      ? "lookup.helperSearching"
                      : "lookup.helperOpening",
                )}
              </span>{" "}
              <span>{t("lookup.waited", { n: waited })}</span>
            </p>
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="ghost"
              disabled={stop.isPending || hand.isPending}
              onClick={() => stop.mutate()}
            >
              {t("lookup.helperCancel")}
            </button>
          </div>
        ) : (
          <>
            {jobState === "failed" && (
              <p className="outside-lookup-error" role="alert">
                {t("lookup.helperFailed", {
                  reason: job.data?.error ?? "",
                })}
              </p>
            )}
            {handError && (
              <p className="outside-lookup-error" role="alert">
                {handError}
              </p>
            )}
            {isSearch && targetList.length > 0 && (
              <div
                className="outside-lookup-targets"
                role="radiogroup"
                aria-label={t("lookup.targetLegend")}
              >
                <span className="outside-lookup-label" aria-hidden="true">
                  {t("lookup.targetLegend")}
                </span>
                {targetList.map((tg, at) => (
                  <div key={tg.name} className="outside-lookup-target">
                    <label>
                      <input
                        type="radio"
                        name={`${ids}-target`}
                        checked={tg.name === searchName}
                        aria-describedby={tg.description ? `${ids}-t-${at}` : undefined}
                        onChange={() => setChosen(tg.name)}
                      />
                      {tg.name}
                      {aiMark(tg.name)}
                    </label>
                    {tg.description && (
                      <p id={`${ids}-t-${at}`} className="outside-lookup-target-desc">
                        {tg.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
            {(isSearch ||
              needsNod ||
              jobState === "failed" ||
              jobState === "cancelled") &&
              // the destinations on their way: no unnamed 「搜尋」 flashing
              // before 「Google search」 (review round 2)
              !(isSearch && targets.isPending) && (
                <div className="outside-lookup-actions">
                  <button
                    type="button"
                    className="btn"
                    data-size="sm"
                    data-variant="primary"
                    onClick={() =>
                      isSearch ? ifTyped(() => hand.mutate(false)) : hand.mutate(false)
                    }
                  >
                    {goLabel(t, { isSearch, again: jobState === "failed", searchName })}
                  </button>
                </div>
              )}
          </>
        )}
        {jobState !== "done" && (
          // 「自己查」 is a disclosure (§3.5 展開區): it says whether it is open
          // and closes again — GOV.UK Design System, Details; NN/g,
          // "Progressive Disclosure".
          <button
            type="button"
            className="btn"
            data-size="sm"
            data-variant="ghost"
            aria-expanded={byHand}
            onClick={() => setByHand((open) => !open)}
          >
            <Icon name={byHand ? "chev_d" : "chev_r"} size={12} />{" "}
            {t("lookup.byHand")}
          </button>
        )}
      </div>
    ) : null;

  const copy = (text: string) => {
    const write = navigator.clipboard?.writeText(text) ?? Promise.reject(new Error("no clipboard"));
    write.then(
      () => setCopied("copied"),
      () => setCopied("failed"),
    );
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
      {helperAvailable && helper.isSuccess && release.data && !online && !helping && !locked && (
        <p className="outside-lookup-hint">
          {t("lookup.helperOffline")}{" "}
          <a href={`${API_BASE}/ai-workspace-helper`} target="_blank" rel="noopener noreferrer">
            {t("lookup.helperInstall")}
          </a>
        </p>
      )}

      {isSearch ? (
        <div className="outside-lookup-section">
          <span className="outside-lookup-label">{t("lookup.suggested")}</span>
          <div className="outside-lookup-actions">
            <code className="outside-lookup-url">{lookup.query}</code>
            <button
              type="button"
              className="btn"
              data-size="sm"
              data-variant="ghost"
              onClick={() => copy(lookup.query ?? "")}
            >
              {copied === "copied" ? t("lookup.copied") : t("lookup.copySuggestion")}
            </button>
          </div>
          {copied === "failed" && (
            <p className="outside-lookup-hint" role="status">
              {t("lookup.copyFailed")}
            </p>
          )}
          <label className="outside-lookup-label" htmlFor={`${ids}-q`}>
            {t("lookup.query")}
          </label>
          <input
            ref={queryBox}
            autoComplete="off"
            id={`${ids}-q`}
            className="input"
            value={query}
            placeholder={t("lookup.queryPlaceholder")}
            readOnly={locked}
            aria-invalid={needKeywords || undefined}
            aria-describedby={needKeywords ? `${ids}-q-need` : undefined}
            onChange={(e) => {
              setQuery(e.target.value);
              setNeedKeywords(false);
            }}
          />
          {needKeywords && (
            <p id={`${ids}-q-need`} className="outside-lookup-error" role="alert">
              {t("lookup.typeKeywords")}
            </p>
          )}
          {manual && (
          <>
          <ul className="outside-lookup-targets">
            {targetList.map((tg, at) =>
              // T5: `preview: false` — the card draws no new-tab button for it
              tg.preview === false ? null : (
                <li key={tg.name} className="outside-lookup-target">
                  <div className="outside-lookup-actions">
                    <button
                      type="button"
                      className="btn"
                      data-size="sm"
                      data-variant="secondary"
                      aria-label={t("lookup.target", {
                        name: t("lookup.searchWith", { name: tg.name }),
                      })}
                      aria-describedby={tg.description ? `${ids}-m-${at}` : undefined}
                      onClick={() =>
                        ifTyped(() => {
                          setTarget(tg.name);
                          window.open(searchUrl(tg, query.trim()), "_blank", NEW_TAB);
                        })
                      }
                    >
                      {t("lookup.searchWith", { name: tg.name })} <span aria-hidden="true">↗</span>
                    </button>
                    {aiMark(tg.name)}
                  </div>
                  {tg.description && (
                    <p id={`${ids}-m-${at}`} className="outside-lookup-target-desc">
                      {tg.description}
                    </p>
                  )}
                </li>
              ),
            )}
          </ul>
          <p className="outside-lookup-hint">{t("lookup.newTab")}</p>
          </>
          )}
        </div>
      ) : (
        <div className="outside-lookup-section">
          <span className="outside-lookup-label">{t("lookup.url")}</span>
          <code className="outside-lookup-url">{lookup.url}</code>
          {manual && (
          <>
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
              {copied === "copied" ? t("lookup.copied") : t("lookup.copyUrl")}
            </button>
          </div>
          {copied === "failed" && (
            <p className="outside-lookup-hint" role="status">
              {t("lookup.copyFailed")}
            </p>
          )}
          <p className="outside-lookup-hint">{t("lookup.newTab")}</p>
          </>
          )}
        </div>
      )}

      {helperBlock}

      {!manual ? null : giveUp ? (
        <div className="outside-lookup-section">
          <label className="outside-lookup-label" htmlFor={`${ids}-r`}>
            {t("lookup.reason")}
          </label>
          <input
            autoComplete="off"
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
            <p className="outside-lookup-hint">
              {t(canAttach ? "lookup.foundHint" : "lookup.foundHintText")}
            </p>
            <textarea
              id={`${ids}-c`}
              className="input outside-lookup-paste"
              rows={6}
              value={content}
              readOnly={locked}
              onChange={(e) => setContent(e.target.value)}
              onPaste={(e) => {
                if (locked) return;
                // D10: a pasted image (a screenshot, "copy image") is an
                // attachment — a text box has nowhere to put it.
                const images = Array.from(e.clipboardData.files ?? []).filter((f) =>
                  f.type.startsWith("image/"),
                );
                if (images.length) {
                  if (canAttach) setFiles((f) => [...f, ...images]);
                  else setHint(t("lookup.pasteImageRefused"));
                }
                const html = e.clipboardData.getData("text/html");
                if (!html) {
                  // Plain text: the browser's own paste. An image alone has no
                  // text to put in the box.
                  if (images.length) e.preventDefault();
                  return;
                }
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
              autoComplete="off"
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
      ) : !manual ? null : giveUp ? (
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

/** The helper's button: the words doing it by hand uses (NN/g heuristic #4,
 * consistency), and the same again after a failure. */
function goLabel(
  t: ReturnType<typeof useT>,
  { isSearch, again, searchName }: { isSearch: boolean; again: boolean; searchName?: string },
): string {
  if (isSearch) {
    // the deploy's list unread: the backend searches its first target, so the
    // button still searches — it just cannot name where (review round 1)
    if (!searchName) return t(again ? "lookup.searchAgainUnnamed" : "lookup.searchUnnamed");
    return t(again ? "lookup.searchAgain" : "lookup.searchWith", { name: searchName });
  }
  return t(again ? "lookup.openAgain" : "lookup.openUrl");
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
