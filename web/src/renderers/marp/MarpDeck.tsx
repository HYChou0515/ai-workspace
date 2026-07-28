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

// Injected into the shadow root (after marp's own css, so it wins ties): each
// fixed 1280×720 slide `<section>` is wrapped (below) in a `.marp-slide-box`
// that becomes a responsive 16:9 box; the section is absolutely positioned and
// CSS-scaled by `--marp-scale` (= paneWidth / 1280, inherited from the
// light-DOM host).
const SCALE_CSS = `
.marp-slides { display: block; }
.marp-slide-box {
  position: relative;
  width: 100%;
  aspect-ratio: 1280 / 720;
  overflow: hidden;
  margin: 0 0 1rem;
  scroll-snap-align: start;
}
.marp-slide-box > section {
  position: absolute;
  top: 0;
  left: 0;
  transform: scale(var(--marp-scale, 1));
  transform-origin: top left;
}
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
    // Wrap each slide <section> in a fit box so the fixed 1280×720 slide can be
    // scaled to the pane width without leaving 1280px-wide layout gaps.
    for (const section of shadow.querySelectorAll(".marpit > section")) {
      const box = document.createElement("div");
      box.className = "marp-slide-box";
      section.parentNode?.insertBefore(box, section);
      box.appendChild(section);
    }
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

  // Bring the active slide into view as it changes while presenting.
  useEffect(() => {
    if (!present) return;
    const boxes = hostRef.current?.shadowRoot?.querySelectorAll<HTMLElement>(".marp-slide-box");
    boxes?.[active]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [present, active]);

  if (!result.ok) {
    return <div className="ev-marp__error">Couldn’t render this Marp deck: {result.error}</div>;
  }
  return (
    <div ref={containerRef} className={present ? "ev-marp ev-marp--present" : "ev-marp"}>
      <div className="ev-marp__toolbar">
        {count > 0 && (
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
