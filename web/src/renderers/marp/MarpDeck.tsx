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

// Injected into the shadow root (after marp's own css, so it wins ties): turn
// each fixed 1280×720 `.marpit-slide` into a responsive 16:9 box whose inner
// <section> is absolutely positioned and CSS-scaled by `--marp-scale`
// (= paneWidth / 1280, inherited from the light-DOM host).
const SCALE_CSS = `
.marp-slides { display: block; }
.marpit-slide {
  position: relative;
  width: 100%;
  aspect-ratio: 1280 / 720;
  overflow: hidden;
  margin: 0 0 1rem;
}
.marpit-slide > section {
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  const result = useMemo(() => {
    try {
      const { html, css } = render(text);
      const rw = rewriteMarpAssets(html, css, resolveAsset);
      return { ok: true as const, html: DOMPurify.sanitize(rw.html), css: rw.css };
    } catch (e) {
      return { ok: false as const, error: e instanceof Error ? e.message : String(e) };
    }
  }, [text, resolveAsset, render]);

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

  if (!result.ok) {
    return <div className="ev-marp__error">Couldn’t render this Marp deck: {result.error}</div>;
  }
  return (
    <div className="ev-marp">
      <div ref={scrollRef} className="ev-marp__scroll">
        <div ref={hostRef} data-testid="marp-host" className="ev-marp__host" />
      </div>
    </div>
  );
}
