/**
 * MarpDeck — renders a Marp markdown deck as a scroll-stack of slides in the
 * workspace file preview. The official marp-core output ({ html, css }) is
 * asset-rewritten (workspace-relative images → file API), sanitized, and
 * injected into a **shadow root** so marp's global theme CSS is isolated from
 * the app both ways. Each fixed 1280×720 slide is scaled to fill the pane width.
 */
import DOMPurify from "dompurify";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { rewriteMarpAssets, slideScale } from "./marpDeck";
import "./marp.css";
import { renderMarp } from "./renderMarp";

// Injected into the shadow root AFTER marp's own css (so it wins ties on the
// same `div.marpit > section` specificity). Marp's theme keeps sizing each slide
// at a fixed 1280×720 and styling it; we only CSS-scale it to the pane width via
// `--marp-scale` (= paneWidth / 1280, inherited from the light-DOM host) and
// collapse the leftover layout box: the negative bottom margin claws back the
// unscaled height, and the host's overflow-x:hidden clips the unscaled width.
// The slides stay direct children of `.marpit` so the theme selector still
// matches — wrapping them elsewhere silently strips the whole theme.
const SCALE_CSS = `
.marp-slides { display: block; }
div.marpit { display: block; }
div.marpit > section {
  transform: scale(var(--marp-scale, 1));
  transform-origin: top left;
  margin: 0 0 calc(720px * (var(--marp-scale, 1) - 1) + 1rem) 0;
  scroll-snap-align: start;
}

/* Present mode: show only the active slide, centred, scaled to fit the whole
   viewport (min of width/height fit) rather than the pane-width stack scale. */
.marp-slides[data-present] {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.marp-slides[data-present] div.marpit { display: contents; }
.marp-slides[data-present] div.marpit > section {
  display: none;
  margin: 0;
  transform: scale(var(--present-scale, 1));
  transform-origin: center center;
}
.marp-slides[data-present] div.marpit > section[data-active] { display: block; }
`;

export type MarpDeckProps = {
  text: string;
  /** Resolve a workspace-relative asset path to a fetchable URL (file API). */
  resolveAsset: (src: string) => string;
  /** Injectable for tests; defaults to the real marp-core renderer. */
  render?: (text: string) => { html: string; css: string };
};

export function MarpDeck({ text, resolveAsset, render = renderMarp }: MarpDeckProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [present, setPresent] = useState(false);
  const [active, setActive] = useState(0);

  const result = useMemo(() => {
    try {
      const { html, css } = render(text);
      const rw = rewriteMarpAssets(html, css, resolveAsset);
      const clean = DOMPurify.sanitize(rw.html);
      const count = (clean.match(/<section\b/gi) ?? []).length;
      return { ok: true as const, html: clean, css: rw.css, count };
    } catch (e) {
      return { ok: false as const, error: e instanceof Error ? e.message : String(e) };
    }
  }, [text, resolveAsset, render]);
  const count = result.ok ? result.count : 0;

  // Inject the (trusted, marp-generated) css + sanitized html into the shadow
  // root. The css is not re-sanitized — it is injected as its own <style>.
  useLayoutEffect(() => {
    if (!result.ok) return;
    const host = hostRef.current;
    if (!host) return;
    const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" });
    shadow.innerHTML = `<style>${result.css}</style><style>${SCALE_CSS}</style><div class="marp-slides">${result.html}</div>`;
  }, [result]);

  // Measure the pane and derive the fit-to-width scale.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setScale(slideScale(el.clientWidth || 1280));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Custom properties inherit across the shadow boundary, so setting the scale
  // on the light-DOM host reaches the injected slides via `var(--marp-scale)`.
  useEffect(() => {
    hostRef.current?.style.setProperty("--marp-scale", String(scale));
  }, [scale, result]);

  const startPresent = () => {
    setActive(0);
    setPresent(true);
    const el = containerRef.current;
    if (el?.requestFullscreen) void el.requestFullscreen().catch(() => {});
  };

  // While presenting, the arrow keys step through slides (clamped at the ends).
  useEffect(() => {
    if (!present) return;
    const onKey = (e: KeyboardEvent) => {
      if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(e.key)) {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, Math.max(0, count - 1)));
      } else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key)) {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [present, count]);

  // Leaving fullscreen (Esc, or the browser's own exit) ends present mode.
  useEffect(() => {
    const onFs = () => {
      if (!document.fullscreenElement) setPresent(false);
    };
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  // Toggle present layout in the shadow: flag the container and mark the one
  // active slide so the CSS above shows only it, centred.
  useEffect(() => {
    const sr = hostRef.current?.shadowRoot;
    const slides = sr?.querySelector(".marp-slides");
    if (!slides) return;
    slides.toggleAttribute("data-present", present);
    sr?.querySelectorAll<HTMLElement>(".marpit > section").forEach((s, i) => {
      s.toggleAttribute("data-active", present && i === active);
    });
  }, [present, active, result]);

  // Scale the active slide to fit the ACTUAL present surface while presenting —
  // measured, not `window`, so it fits whether or not the fullscreen request
  // grew the element (headless / blocked fullscreen keep the pane's own size).
  useEffect(() => {
    if (!present) return;
    const el = scrollRef.current;
    if (!el) return;
    const setPresentScale = () => {
      const s = Math.min(el.clientWidth / 1280, el.clientHeight / 720);
      hostRef.current?.style.setProperty("--present-scale", String(s || 1));
    };
    setPresentScale();
    const ro = new ResizeObserver(setPresentScale);
    ro.observe(el);
    return () => ro.disconnect();
  }, [present]);

  if (!result.ok) {
    return <div className="ev-marp__error">Couldn’t render this Marp deck: {result.error}</div>;
  }
  return (
    <div ref={containerRef} className={present ? "ev-marp ev-marp--present" : "ev-marp"}>
      <div className="ev-marp__toolbar">
        {!present && count > 0 && (
          <button type="button" className="ev-marp__present-btn" onClick={startPresent}>
            Present
          </button>
        )}
        {present && (
          <span data-testid="marp-present-counter" className="ev-marp__counter" aria-live="polite">
            {active + 1} / {count}
          </span>
        )}
      </div>
      <div ref={scrollRef} className="ev-marp__scroll">
        <div ref={hostRef} data-testid="marp-host" className="ev-marp__host" />
      </div>
    </div>
  );
}
