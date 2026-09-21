/**
 * ExportDialog (plan-chat-video-export P8) — the chat header's Export: the
 * conversation as text (JSON, Markdown) or as a video, over a range of
 * messages counted from the newest.
 *
 * One dialog rather than a menu that opens one: the header's action row
 * steps down to a single ⋯ menu in a narrow column, and a submenu inside
 * that tier is not a thing it can draw. The three formats are one choice at
 * the top; the video's own controls appear under it.
 *
 * The video is made from the API a script would use: the transcript is the
 * export route's JSON, sliced here by the same `absoluteRange` the text
 * export sends as `?start&end`, then POSTed with the options. The dialog
 * never invents a count or a size — the thread's length comes from the
 * transcript, the ceilings from the deployment (`GET …/chat-video`), the
 * size from `lib/videoSize`.
 *
 * Drawn from the parts the app already has (the rule #825 set for the
 * Sandbox modal): the frame is `ToolsPickerModal`'s — 480px, a `<strong>`
 * title over a 12px lede, a `.btn` Cancel / primary footer, no ✕ and no
 * icon (Escape and Cancel are the exits) — every field a label over the
 * house `.input`, the slider `FontSizeSlider`'s, helpers as `.detail`.
 *
 * No `useDirtyClose` (the plan's 知情取捨): a few dropdowns are not unsaved
 * work, and a "discard changes?" after one radio click is the guard #779
 * warns against — it fires when nothing was lost and teaches people to
 * click through it.
 */

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useId, useMemo, useState } from "react";

import {
  type ChatTranscript,
  type ChatVideoLimits,
  type ChatVideoQueued,
  type ChatVideoRequest,
  fetchChatTranscript,
  fetchChatVideoLimits,
  startChatVideo,
} from "../api/chatVideo";
import { qk } from "../api/queryKeys";
import { type ChatExportOptions, downloadChatExport } from "../api/workflows";
import { absoluteRange, newestNumber, type RangeChoice } from "../lib/chatExportRange";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import {
  allowedSteps,
  allowedTextSizes,
  type Aspect,
  ASPECTS,
  estimateMegabytes,
  fitChoice,
  resolveSize,
  type SizeChoice,
  TEXT_SIZES,
} from "../lib/videoSize";
import { ModalShell } from "./ModalShell";

export type ExportDialogClient = {
  fetchChatTranscript: typeof fetchChatTranscript;
  downloadChatExport: (
    slug: string,
    itemId: string,
    chatId: string,
    options: ChatExportOptions,
  ) => Promise<void>;
  startChatVideo: (slug: string, itemId: string, body: ChatVideoRequest) => Promise<ChatVideoQueued>;
  fetchChatVideoLimits: typeof fetchChatVideoLimits;
};

const realClient: ExportDialogClient = {
  fetchChatTranscript,
  downloadChatExport,
  startChatVideo,
  fetchChatVideoLimits,
};

type Kind = "json" | "md" | "video";
type Fmt = "mp4" | "gif" | "webm";
const FORMATS: Fmt[] = ["mp4", "gif", "webm"];

/** The tempo knobs of decision 9 — speed, typing, the composer push-in and
 * the length; with the size and the format that is the six, and every other
 * `VideoOptions` field is the struct's default. Defaults and bounds are the
 * struct's own (`VideoOptions.__post_init__`); a value typed past a bound is
 * clamped at submit rather than sent for a 422. */
type Tempo = {
  type_ms: number;
  zoom: number;
  speed: number;
  max_seconds: number;
};
const TEMPO_DEFAULTS: Tempo = { type_ms: 55, zoom: 1.8, speed: 1, max_seconds: 90 };
const TEMPO_BOUNDS: Record<keyof Tempo, { min: number; max: number; step: number }> = {
  type_ms: { min: 0, max: 10000, step: 1 },
  zoom: { min: 1, max: 5, step: 0.1 },
  speed: { min: 0.1, max: 100, step: 0.1 },
  max_seconds: { min: 1, max: 3600, step: 1 },
};
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

/** The plan's names for the four text sizes. */
const TEXT_SIZE_NAME: Record<number, "small" | "medium" | "large" | "xlarge"> = {
  0.8: "small",
  1: "medium",
  1.3: "large",
  1.6: "xlarge",
};

type Form = {
  kind: Kind;
  range: RangeChoice;
  fmt: Fmt;
  size: SizeChoice;
  tempo: Tempo;
};

const INITIAL: Form = {
  kind: "json",
  range: { kind: "all" },
  fmt: "mp4",
  size: { mode: "resolution", aspect: "16:9", p: 720 },
  tempo: TEMPO_DEFAULTS,
};

const ROLE_GLYPH: Record<string, string> = { user: "👤", assistant: "🤖", tool: "🔧" };

/** `#k · 👤 first words` — what a message is called in the from/to pickers. */
function messageLabel(total: number, index: number, m: ChatTranscript["messages"][number]): string {
  const head = (m.content || "").replace(/\s+/g, " ").trim().slice(0, 24);
  return `#${newestNumber(total, index)} · ${ROLE_GLYPH[m.role] ?? "·"} ${head}`;
}

/** The 16:9 frame with the ceiling's pixel count — `1920×1080` for
 * 2,073,600 — the way the server's own refusal names it (`_side_of`). */
function ceilingFrame(limits: ChatVideoLimits): { w: number; h: number } {
  const w = Math.round(Math.sqrt((limits.max_pixels * 16) / 9));
  return { w, h: Math.round((w * 9) / 16) };
}

/** A section: its heading in the caps-label style, the controls under it. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="export-dialog__section">
      <div className="export-dialog__label">{label}</div>
      {children}
    </section>
  );
}

export function ExportDialog({
  slug,
  itemId,
  chatId,
  canExportVideo,
  onClose,
  onVideoQueued,
  client = realClient,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  /** `read_content` + `add_content` — the two verbs `POST …/chat-video` asks.
   * Without them the video choice is drawn locked, with the reason, rather
   * than absent: the person learns what to ask for. */
  canExportVideo: boolean;
  onClose: () => void;
  onVideoQueued: (queued: ChatVideoQueued) => void;
  client?: ExportDialogClient;
}) {
  const t = useT();
  const titleId = useId();
  const [form, setForm] = useState<Form>(INITIAL);
  const [custom, setCustom] = useState({ from: 1, to: 1 });
  const [latestN, setLatestN] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const transcript = useQuery({
    queryKey: qk.chatTranscript(itemId, chatId),
    queryFn: () => client.fetchChatTranscript(slug, itemId, chatId),
  });
  const limits = useQuery({
    queryKey: qk.chatVideoLimits(itemId),
    queryFn: () => client.fetchChatVideoLimits(slug, itemId),
    enabled: canExportVideo,
  });

  const messages = transcript.data?.messages ?? [];
  const total = messages.length;
  const range: RangeChoice =
    form.range.kind === "latest"
      ? { kind: "latest", n: latestN }
      : form.range.kind === "custom"
        ? { kind: "custom", from: custom.from, to: custom.to }
        : form.range;
  const abs = absoluteRange(total, range);

  const ceiling = limits.data;
  // What the person chose, as this deployment can honour it — the stop
  // or text size the ceiling allows nearest to it (`fitChoice`).
  const choice = useMemo(() => fitChoice(form.size, ceiling?.max_pixels), [form.size, ceiling]);
  const size = useMemo(() => resolveSize(choice), [choice]);
  const tooBig = ceiling !== undefined && size.width * size.height > ceiling.max_pixels;
  /** The knobs as they will be sent: each within the struct's bounds, the
   * length also within the deployment's. */
  const tempo: Tempo = {
    type_ms: clamp(form.tempo.type_ms, TEMPO_BOUNDS.type_ms.min, TEMPO_BOUNDS.type_ms.max),
    zoom: clamp(form.tempo.zoom, TEMPO_BOUNDS.zoom.min, TEMPO_BOUNDS.zoom.max),
    speed: clamp(form.tempo.speed, TEMPO_BOUNDS.speed.min, TEMPO_BOUNDS.speed.max),
    max_seconds: clamp(form.tempo.max_seconds, 1, ceiling?.max_seconds ?? TEMPO_BOUNDS.max_seconds.max),
  };
  const megabytes = estimateMegabytes(form.fmt, size.width * size.height, tempo.max_seconds);

  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));
  const patchTempo = (p: Partial<Tempo>) => setForm((f) => ({ ...f, tempo: { ...f.tempo, ...p } }));
  const setSize = (s: SizeChoice) => patch({ size: s });

  const ready = transcript.isSuccess && total > 0 && (form.kind !== "video" || ceiling !== undefined);
  const canSubmit = ready && !busy && !(form.kind === "video" && tooBig);

  const submit = async () => {
    if (!canSubmit || !transcript.data) return;
    setBusy(true);
    setError(null);
    try {
      if (form.kind === "video") {
        const queued = await client.startChatVideo(slug, itemId, {
          transcript: {
            title: transcript.data.title,
            messages: abs ? messages.slice(abs.start, abs.end) : messages,
          },
          options: {
            width: size.width,
            height: size.height,
            // 0 = the player's automatic rule; a text-size choice pins it.
            scale: size.scaleIsAuto ? 0 : size.scale,
            fmt: [form.fmt],
            ...tempo,
          },
          output_path: null,
        });
        onVideoQueued(queued);
      } else {
        await client.downloadChatExport(slug, itemId, chatId, { format: form.kind, range: abs });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const steps = (aspect: Aspect) => allowedSteps(aspect, ceiling?.max_pixels);
  const textSizes = (aspect: Aspect) => allowedTextSizes(aspect, ceiling?.max_pixels);

  const textSizeLabel = (s: number) => `${t(`export.video.textSize.${TEXT_SIZE_NAME[s]}`)}（${s}×）`;

  return (
    <ModalShell
      onClose={onClose}
      labelledBy={titleId}
      data-testid="export-dialog"
      width={480}
      maxWidth="92vw"
      panelClassName="export-dialog"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <strong id={titleId} style={{ fontSize: pxToRem(14) }}>
        {t("export.title")}
      </strong>
      <p className="export-dialog__lede">{t("export.desc")}</p>

      <Field label={t("export.kind")}>
        <div className="export-dialog__choices" role="radiogroup" aria-label={t("export.kind")}>
          {(["json", "md", "video"] as Kind[]).map((k) => (
            <label key={k} className="export-dialog__choice">
              <input
                type="radio"
                name="export-kind"
                data-testid={`export-kind-${k}`}
                checked={form.kind === k}
                disabled={k === "video" && !canExportVideo}
                onChange={() => patch({ kind: k })}
              />
              {t(`export.kind.${k}`)}
            </label>
          ))}
        </div>
        {!canExportVideo && <p className="detail">{t("export.kind.video.locked")}</p>}
      </Field>

      <Field label={t("export.range")}>
        {transcript.isPending ? (
          <p className="detail">{t("export.loading")}</p>
        ) : transcript.isError ? (
          <p className="detail export-dialog__error" role="alert">
            {t("export.loadFailed")}
          </p>
        ) : (
          <>
            <div className="export-dialog__choices" role="radiogroup" aria-label={t("export.range")}>
              <label className="export-dialog__choice">
                <input
                  type="radio"
                  name="export-range"
                  data-testid="export-range-all"
                  checked={form.range.kind === "all"}
                  onChange={() => patch({ range: { kind: "all" } })}
                />
                {t("export.range.all", { n: total })}
              </label>
              <label className="export-dialog__choice">
                <input
                  type="radio"
                  name="export-range"
                  data-testid="export-range-latest"
                  checked={form.range.kind === "latest"}
                  onChange={() => patch({ range: { kind: "latest", n: latestN } })}
                />
                {t("export.range.latest")}
                <input
                  type="number"
                  data-testid="export-latest-n"
                  className="input export-dialog__num"
                  min={1}
                  max={total}
                  value={latestN}
                  onChange={(e) => {
                    const n = Math.max(1, Math.min(total, Number(e.target.value) || 1));
                    setLatestN(n);
                    patch({ range: { kind: "latest", n } });
                  }}
                />
                {t("export.range.latestUnit")}
              </label>
              <label className="export-dialog__choice">
                <input
                  type="radio"
                  name="export-range"
                  data-testid="export-range-custom"
                  checked={form.range.kind === "custom"}
                  onChange={() =>
                    patch({ range: { kind: "custom", from: custom.from, to: custom.to } })
                  }
                />
                {t("export.range.custom")}
              </label>
            </div>
            {form.range.kind === "custom" && (
              <>
                <div className="export-dialog__grid">
                  {(["from", "to"] as const).map((end) => (
                    <div key={end} className="export-dialog__field">
                      <label htmlFor={`export-${end}`}>{t(`export.range.${end}`)}</label>
                      <select
                        id={`export-${end}`}
                        data-testid={`export-${end}`}
                        className="input input--block"
                        value={custom[end]}
                        onChange={(e) => {
                          const next = { ...custom, [end]: Number(e.target.value) };
                          setCustom(next);
                          patch({ range: { kind: "custom", from: next.from, to: next.to } });
                        }}
                      >
                        {messages
                          .map((m, i) => ({
                            k: newestNumber(total, i),
                            label: messageLabel(total, i, m),
                          }))
                          .sort((a, b) => a.k - b.k)
                          .map(({ k, label }) => (
                            <option key={k} value={k}>
                              {label}
                              {k === 1 ? `（${t("export.range.newest")}）` : ""}
                              {k === total ? `（${t("export.range.oldest")}）` : ""}
                            </option>
                          ))}
                      </select>
                    </div>
                  ))}
                </div>
                <p className="detail">{t("export.range.note")}</p>
              </>
            )}
          </>
        )}
      </Field>

      {form.kind === "video" &&
        (ceiling === undefined ? (
          limits.isError ? (
            // No ceiling known at all. (A failed REFETCH keeps the last
            // ceiling — the query's data survives the error — and the
            // controls are drawn from it: `ready` reads the same `ceiling`,
            // so the sentence and the button never disagree.)
            <p className="detail export-dialog__error" role="alert" data-testid="export-limits-error">
              {t("export.limitsFailed")}
            </p>
          ) : (
            <p className="detail">{t("export.limitsLoading")}</p>
          )
        ) : (
          <>
            <Field label={t("export.video.size")}>
              <div
                className="export-dialog__choices"
                role="radiogroup"
                aria-label={t("export.video.size")}
              >
                {(["resolution", "text", "custom"] as const).map((mode) => (
                  <label key={mode} className="export-dialog__choice">
                    <input
                      type="radio"
                      name="export-size-mode"
                      data-testid={`export-size-mode-${mode}`}
                      checked={form.size.mode === mode}
                      onChange={() => {
                        const aspect = "aspect" in form.size ? form.size.aspect : "16:9";
                        // 720p, as the dialog opens; `fitChoice` brings it under the ceiling.
                        if (mode === "resolution") setSize({ mode, aspect, p: 720 });
                        else if (mode === "text") setSize({ mode, aspect, textScale: 1 });
                        else setSize({ mode, width: size.width, height: size.height });
                      }}
                    />
                    {t(`export.video.size.${mode}`)}
                  </label>
                ))}
              </div>
              <div className="export-dialog__grid">
                <div className="export-dialog__field">
                  <label htmlFor="export-fmt">{t("export.video.format")}</label>
                  <select
                    id="export-fmt"
                    data-testid="export-fmt"
                    className="input input--block"
                    value={form.fmt}
                    onChange={(e) => patch({ fmt: e.target.value as Fmt })}
                  >
                    {FORMATS.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                </div>
                {form.size.mode !== "custom" && (
                  <div className="export-dialog__field">
                    <label htmlFor="export-aspect">{t("export.video.aspect")}</label>
                    <select
                      id="export-aspect"
                      data-testid="export-aspect"
                      className="input input--block"
                      value={form.size.aspect}
                      onChange={(e) => {
                        // The stop / text size follows through `fitChoice`.
                        const aspect = e.target.value as Aspect;
                        if (form.size.mode !== "custom") setSize({ ...form.size, aspect });
                      }}
                    >
                      {ASPECTS.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {choice.mode === "resolution" && (
                  <div className="export-dialog__field">
                    <label htmlFor="export-resolution">{t("export.video.resolution")}</label>
                    {/* A slider over the named stops this deployment allows
                        (the plan's 解析度滑桿; "用拉的"): the value is the
                        stop's index, the label beside it the stop's name. */}
                    <div className="export-dialog__slider">
                      <input
                        id="export-resolution"
                        type="range"
                        data-testid="export-resolution"
                        data-steps={steps(choice.aspect).join(",")}
                        min={0}
                        max={Math.max(0, steps(choice.aspect).length - 1)}
                        step={1}
                        value={Math.max(0, steps(choice.aspect).indexOf(choice.p))}
                        onChange={(e) => {
                          if (choice.mode !== "resolution") return;
                          const p = steps(choice.aspect)[Number(e.target.value)];
                          if (p !== undefined) setSize({ ...choice, p });
                        }}
                      />
                      <span data-testid="export-resolution-label">{choice.p}p</span>
                    </div>
                  </div>
                )}
                {choice.mode === "text" && (
                  <div className="export-dialog__field">
                    <label htmlFor="export-text-size">{t("export.video.textSize")}</label>
                    <select
                      id="export-text-size"
                      data-testid="export-text-size"
                      className="input input--block"
                      value={choice.textScale}
                      onChange={(e) =>
                        choice.mode === "text" &&
                        setSize({ ...choice, textScale: Number(e.target.value) })
                      }
                    >
                      {textSizes(choice.aspect).map((s) => (
                        <option key={s} value={s}>
                          {textSizeLabel(s)}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {form.size.mode === "custom" && (
                  <>
                    <div className="export-dialog__field">
                      <label htmlFor="export-width">{t("export.video.width")}</label>
                      <input
                        id="export-width"
                        type="number"
                        data-testid="export-width"
                        className="input input--block"
                        min={16}
                        max={7680}
                        step={2}
                        value={form.size.width}
                        onChange={(e) =>
                          form.size.mode === "custom" &&
                          setSize({ ...form.size, width: Number(e.target.value) || 16 })
                        }
                      />
                    </div>
                    <div className="export-dialog__field">
                      <label htmlFor="export-height">{t("export.video.height")}</label>
                      <input
                        id="export-height"
                        type="number"
                        data-testid="export-height"
                        className="input input--block"
                        min={16}
                        max={4320}
                        step={2}
                        value={form.size.height}
                        onChange={(e) =>
                          form.size.mode === "custom" &&
                          setSize({ ...form.size, height: Number(e.target.value) || 16 })
                        }
                      />
                    </div>
                    <div className="export-dialog__field">
                      <label htmlFor="export-custom-text">{t("export.video.textSize")}</label>
                      <select
                        id="export-custom-text"
                        data-testid="export-custom-text"
                        className="input input--block"
                        value={form.size.textScale ?? ""}
                        onChange={(e) => {
                          if (form.size.mode !== "custom") return;
                          const { textScale: _dropped, ...rest } = form.size;
                          setSize(
                            e.target.value ? { ...rest, textScale: Number(e.target.value) } : rest,
                          );
                        }}
                      >
                        <option value="">{t("export.video.textSize.auto")}</option>
                        {TEXT_SIZES.map((s) => (
                          <option key={s} value={s}>
                            {textSizeLabel(s)}
                          </option>
                        ))}
                      </select>
                    </div>
                  </>
                )}
              </div>
              <p className="export-dialog__result" data-testid="export-size-result">
                {t("export.video.result", {
                  w: size.width,
                  h: size.height,
                  s: size.scale.toFixed(2).replace(/\.?0+$/, ""),
                  mb: megabytes < 10 ? megabytes.toFixed(1) : Math.round(megabytes),
                })}
              </p>
              {tooBig && (
                <p className="detail export-dialog__error" role="alert" data-testid="export-size-error">
                  {t("export.video.tooBig", ceilingFrame(ceiling))}
                </p>
              )}
            </Field>

            <Field label={t("export.video.tempo")}>
              <div className="export-dialog__grid">
                {(
                  [
                    ["speed", "export.video.speed"],
                    ["type_ms", "export.video.typeMs"],
                    ["zoom", "export.video.zoom"],
                    ["max_seconds", "export.video.maxSeconds"],
                  ] as const
                ).map(([key, label]) => (
                  <div key={key} className="export-dialog__field">
                    <label htmlFor={`export-${key}`}>{t(label)}</label>
                    <input
                      id={`export-${key}`}
                      type="number"
                      data-testid={`export-${key.replace(/_/g, "-")}`}
                      className="input input--block"
                      min={TEMPO_BOUNDS[key].min}
                      max={key === "max_seconds" ? ceiling.max_seconds : TEMPO_BOUNDS[key].max}
                      step={TEMPO_BOUNDS[key].step}
                      value={form.tempo[key]}
                      onChange={(e) => patchTempo({ [key]: Number(e.target.value) })}
                    />
                  </div>
                ))}
              </div>
              <p className="detail">{t("export.video.maxSeconds.note", { n: ceiling.max_seconds })}</p>
            </Field>
          </>
        ))}

      {error && (
        <p className="detail export-dialog__error" role="alert" data-testid="export-error">
          {t("export.failed", { reason: error })}
        </p>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="export-cancel"
          onClick={onClose}
        >
          {t("export.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-size="sm"
          data-testid="export-submit"
          disabled={!canSubmit}
          onClick={() => void submit()}
        >
          {t(form.kind === "video" ? "export.submit.video" : "export.submit.text")}
        </button>
      </div>
    </ModalShell>
  );
}
