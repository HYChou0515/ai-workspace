/**
 * Image preview (png/jpg/jpeg/gif/svg/webp/bmp). Renders the BUFFER's current
 * bytes as a Blob URL (so an edit shows immediately); the Edit toggle flips to
 * the byte editor like every other file (#all-editable).
 *
 * The image lives in a pan/zoom viewport: mouse wheel zooms toward the cursor
 * (up = in, down = out), drag pans, double-click resets to fit. All the math is
 * the pure reducer in ./panZoom — this component only wires DOM events to it and
 * applies the result as a CSS transform.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { encodeText } from "../api/encoding";
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { imageMime } from "../pages/investigation/renderer";
import {
  type PanZoom,
  type Size,
  canPan,
  fitSize,
  initialState,
  panBy,
  zoomAt,
} from "./panZoom";
import { TextRenderer } from "./TextRenderer";

// Wheel-delta → zoom-factor sensitivity. `exp(-dy * k)`: a standard ~100px
// notch lands near a 1.16× step, smooth across mice and trackpads alike.
const ZOOM_SENSITIVITY = 0.0015;

export function ImageRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  const editing = isEditing(path);

  const url = useMemo(() => {
    if (entry.status !== "ready" || entry.kind !== "text") return null;
    const bytes = encodeText(entry.text, entry.encoding);
    return URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer], { type: imageMime(path) }));
  }, [entry.status, entry.kind, entry.text, entry.encoding, path]);

  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url]);

  // Viewport (measured) + natural image size (from onLoad) → the fitted base.
  const [viewport, setViewport] = useState<Size>({ w: 0, h: 0 });
  const [natural, setNatural] = useState<Size>({ w: 0, h: 0 });
  const base = useMemo(() => fitSize(natural, viewport), [natural, viewport]);
  const [pz, setPz] = useState<PanZoom>({ scale: 1, tx: 0, ty: 0 });

  // Reset to a centered fit whenever the fit changes (image load, pane resize)
  // or the file switches — so a new image never inherits the prior one's zoom.
  useEffect(() => {
    if (base.w > 0 && viewport.w > 0) setPz(initialState(base, viewport));
  }, [base.w, base.h, viewport.w, viewport.h, path]);

  // Fresh state for the native (non-passive) wheel listener's stable closure.
  const stateRef = useRef({ pz, base, viewport });
  stateRef.current = { pz, base, viewport };

  const onWheel = useCallback((e: WheelEvent, el: HTMLDivElement) => {
    // preventDefault needs a non-passive listener (React's onWheel is passive),
    // so this is attached natively below — without it the pane scrolls instead.
    e.preventDefault();
    const { pz: cur, base: b, viewport: vp } = stateRef.current;
    if (b.w <= 0) return;
    const rect = el.getBoundingClientRect();
    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 16; // lines → ~px
    else if (e.deltaMode === 2) dy *= vp.h || 1; // pages → ~px
    const factor = Math.exp(-dy * ZOOM_SENSITIVITY);
    setPz(zoomAt(cur, factor, e.clientX - rect.left, e.clientY - rect.top, b, vp));
  }, []);

  // Measure + observe the viewport and attach the native wheel listener via a
  // callback ref, so it's wired correctly across mount / edit-toggle remounts.
  const cleanupRef = useRef<(() => void) | null>(null);
  const setContainer = useCallback(
    (el: HTMLDivElement | null) => {
      cleanupRef.current?.();
      cleanupRef.current = null;
      if (!el) return;
      const measure = () => setViewport({ w: el.clientWidth, h: el.clientHeight });
      measure();
      const wheel = (e: WheelEvent) => onWheel(e, el);
      el.addEventListener("wheel", wheel, { passive: false });
      let ro: ResizeObserver | null = null;
      if (typeof ResizeObserver !== "undefined") {
        ro = new ResizeObserver(measure);
        ro.observe(el);
      }
      cleanupRef.current = () => {
        el.removeEventListener("wheel", wheel);
        ro?.disconnect();
      };
    },
    [onWheel],
  );

  const drag = useRef<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const pannable = canPan(pz, base, viewport);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!pannable) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY };
    setDragging(true);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x;
    const dy = e.clientY - drag.current.y;
    drag.current = { x: e.clientX, y: e.clientY };
    setPz((p) => panBy(p, dx, dy, base, viewport));
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };

  if (editing) return <TextRenderer path={path} />;
  if (entry.status === "loading" || !url) {
    return <div style={{ color: "var(--text-paper-d)" }}>Loading {path}…</div>;
  }
  return (
    <div
      ref={setContainer}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={() => base.w > 0 && setPz(initialState(base, viewport))}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        touchAction: "none",
        cursor: pannable ? (dragging ? "grabbing" : "grab") : "default",
      }}
    >
      <img
        src={url}
        alt={path}
        draggable={false}
        onLoad={(e) =>
          setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
        }
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: base.w || undefined,
          height: base.h || undefined,
          maxWidth: base.w ? undefined : "100%",
          transform: `translate(${pz.tx}px, ${pz.ty}px) scale(${pz.scale})`,
          transformOrigin: "0 0",
          willChange: "transform",
          userSelect: "none",
        }}
      />
    </div>
  );
}
