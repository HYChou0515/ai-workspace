/**
 * #847/#848 P6 — a shown view file drawn small in its chat card.
 *
 * Which drawing is the VIEW KIND's business: a kind registered with a
 * `Thumbnail` (`viewKindRegistry.tsx`) gets one, any other kind keeps the plain
 * file card. The host decides only when and where: nothing is read until the
 * card first scrolls into view, the thumbnail is mounted once and never again
 * for that card, it sits inside the card's own click target with pointer events
 * off, and any failure — an unreadable file, a spec that does not parse, a
 * thumbnail that reports one or throws — puts the plain card back.
 */
import { useQuery } from "@tanstack/react-query";
import { Component, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { useFileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { parseViewSpec } from "../renderers/entity/shared";
import { resolveViewRenderer } from "../renderers/entity/viewKindRegistry";
import { pickRenderer } from "../renderers/registry";

/** Whether `path` opens as a view (`*.ai.yaml`) — the file registry's own rule. */
export function isViewFile(path: string): boolean {
  return pickRenderer(path) === "aiview";
}

/** A ref to attach, and whether that element has EVER been in view. The
 * observer lets go the first time, so a card is "seen" once and stays so.
 *
 * A callback ref, so the observer follows the element: a card swaps its `<a>`
 * for a `<button>` when the workspace opens, and watching the detached link
 * left the card undrawn for good (found in a real browser). */
export function useSeenOnce<T extends Element>(): [(el: T | null) => void, boolean] {
  const [el, ref] = useState<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen || !el) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setSeen(true);
    });
    io.observe(el);
    // Seen (or a new element), the effect re-runs and this lets go of the old.
    return () => io.disconnect();
  }, [el, seen]);
  return [ref, seen];
}

class ThumbBoundary extends Component<{ onError: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/**
 * The thumbnail of view file `path`, or `null` for the plain card: before
 * `seen`, for a kind that offers none, and after any failure. The caller gives
 * the returned node a sized box; it fills it.
 */
export function useViewThumbnail(path: string, seen: boolean): ReactNode | null {
  const svc = useFileService();
  const [failed, setFailed] = useState(false);
  const onFail = useCallback(() => setFailed(true), []);
  // The live view's own cache entry (`qk.file`): opening the card after its
  // thumbnail was drawn reads nothing twice.
  const file = useQuery({
    queryKey: qk.file(svc.scopeId, path),
    queryFn: () => svc.readFile(path),
    staleTime: Number.POSITIVE_INFINITY,
    enabled: seen && !failed,
  });
  const text = file.data?.kind === "text" ? file.data.text : null;
  const spec = useMemo(() => (text === null ? null : parseViewSpec(text)), [text]);
  const Thumbnail = spec ? resolveViewRenderer(spec.view).Thumbnail : undefined;
  if (!seen || failed || !spec || !Thumbnail) return null;
  return (
    <div data-view-thumbnail style={{ width: "100%", height: "100%", pointerEvents: "none" }}>
      <ThumbBoundary onError={onFail}>
        <Thumbnail spec={spec} path={path} onFail={onFail} />
      </ThumbBoundary>
    </div>
  );
}
