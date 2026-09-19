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
 * icon (Escape and Cancel are the exits, both through `useDirtyClose`) —
 * and every field is a label over the house `.input`, helpers as `.detail`.
 */

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useId, useMemo, useRef, useState } from "react";

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
import { useDirtyClose } from "../hooks/useDirtyClose";
import { absoluteRange, newestNumber, type RangeChoice } from "../lib/chatExportRange";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { sameShape } from "../lib/sameShape";
import {
  type Aspect,
  ASPECTS,
  estimateMegabytes,
  frameFor,
  RESOLUTION_STEPS,
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

/** The tempo knobs, the `VideoOptions` fields they are — the CLI's flags,
 * one for one. Defaults are the struct's own. */
type Tempo = {
  type_ms: number;
  stream_ms: number;
  tool_pause_ms: number;
  zoom: number;
  speed: number;
  max_seconds: number;
};
const TEMPO_DEFAULTS: Tempo = {
  type_ms: 55,
  stream_ms: 22,
  tool_pause_ms: 1200,
  zoom: 1.8,
  speed: 1,
  max_seconds: 90,
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
  const initialRef = useRef(INITIAL);
  const attemptClose = useDirtyClose(!sameShape(form, initialRef.current), onClose);

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

  const size = useMemo(() => resolveSize(form.size), [form.size]);
  const ceiling = limits.data;
  const tooBig = ceiling !== undefined && size.width * size.height > ceiling.max_pixels;
  const maxSeconds = Math.min(form.tempo.max_seconds, ceiling?.max_seconds ?? form.tempo.max_seconds);
  const megabytes = estimateMegabytes(form.fmt, size.width * size.height, maxSeconds);

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
            ...form.tempo,
            max_seconds: maxSeconds,
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

  /** The frames this deployment allows for `aspect`, by short side. */
  const allowedSteps = (aspect: Aspect) =>
    RESOLUTION_STEPS.filter((p) => {
      const f = frameFor(aspect, p);
      return ceiling === undefined || f.width * f.height <= ceiling.max_pixels;
    });
  const allowedTextSizes = (aspect: Aspect) =>
    TEXT_SIZES.filter((s) => {
      const f = frameFor(aspect, 720 * s);
      return ceiling === undefined || f.width * f.height <= ceiling.max_pixels;
    });

  return (
    <ModalShell
      onClose={attemptClose}
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
                        className="input"
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
          <p className="detail">{t("export.loading")}</p>
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
                        if (mode === "resolution")
                          setSize({ mode, aspect, p: allowedSteps(aspect)[0] ?? 480 });
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
                    className="input"
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
                      className="input"
                      value={form.size.aspect}
                      onChange={(e) => {
                        const aspect = e.target.value as Aspect;
                        if (form.size.mode === "resolution") {
                          const steps = allowedSteps(aspect);
                          const p = steps.includes(form.size.p)
                            ? form.size.p
                            : (steps[steps.length - 1] ?? 480);
                          setSize({ mode: "resolution", aspect, p });
                        } else if (form.size.mode === "text") {
                          const sizes = allowedTextSizes(aspect);
                          const textScale = sizes.includes(form.size.textScale)
                            ? form.size.textScale
                            : (sizes[sizes.length - 1] ?? 1);
                          setSize({ mode: "text", aspect, textScale });
                        }
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
                {form.size.mode === "resolution" && (
                  <div className="export-dialog__field">
                    <label htmlFor="export-resolution">{t("export.video.resolution")}</label>
                    <select
                      id="export-resolution"
                      data-testid="export-resolution"
                      className="input"
                      value={form.size.p}
                      onChange={(e) =>
                        form.size.mode === "resolution" &&
                        setSize({ ...form.size, p: Number(e.target.value) })
                      }
                    >
                      {allowedSteps(form.size.aspect).map((p) => (
                        <option key={p} value={p}>
                          {p}p
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {form.size.mode === "text" && (
                  <div className="export-dialog__field">
                    <label htmlFor="export-text-size">{t("export.video.textSize")}</label>
                    <select
                      id="export-text-size"
                      data-testid="export-text-size"
                      className="input"
                      value={form.size.textScale}
                      onChange={(e) =>
                        form.size.mode === "text" &&
                        setSize({ ...form.size, textScale: Number(e.target.value) })
                      }
                    >
                      {allowedTextSizes(form.size.aspect).map((s) => (
                        <option key={s} value={s}>
                          {s}×
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
                        className="input"
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
                        className="input"
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
                    ["type_ms", "export.video.typeMs", 0, 10000, 1],
                    ["stream_ms", "export.video.streamMs", 0, 10000, 1],
                    ["tool_pause_ms", "export.video.toolPauseMs", 0, 60000, 100],
                    ["zoom", "export.video.zoom", 1, 5, 0.1],
                    ["speed", "export.video.speed", 0.1, 100, 0.1],
                  ] as const
                ).map(([key, label, min, max, step]) => (
                  <div key={key} className="export-dialog__field">
                    <label htmlFor={`export-${key}`}>{t(label)}</label>
                    <input
                      id={`export-${key}`}
                      type="number"
                      data-testid={`export-${key.replace(/_/g, "-")}`}
                      className="input"
                      min={min}
                      max={max}
                      step={step}
                      value={form.tempo[key]}
                      onChange={(e) => patchTempo({ [key]: Number(e.target.value) })}
                    />
                  </div>
                ))}
                <div className="export-dialog__field">
                  <label htmlFor="export-max_seconds">{t("export.video.maxSeconds")}</label>
                  <input
                    id="export-max_seconds"
                    type="number"
                    data-testid="export-max-seconds"
                    className="input"
                    min={1}
                    max={ceiling.max_seconds}
                    value={form.tempo.max_seconds}
                    onChange={(e) =>
                      patchTempo({
                        max_seconds: Math.max(
                          1,
                          Math.min(ceiling.max_seconds, Number(e.target.value) || 1),
                        ),
                      })
                    }
                  />
                </div>
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
          onClick={attemptClose}
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
