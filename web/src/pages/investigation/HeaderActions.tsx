/**
 * The chat header's action buttons, in the shape the column has room for.
 *
 * Wide, they are icon + label. When they no longer fit beside the title they
 * step down to icons alone (the label moves into the tooltip), and when even
 * those do not fit, to one "⋯" with the same actions behind it. Before this the
 * row simply wrapped — at a 390px viewport the header was three rows tall,
 * every one of them taken from the message area, in the column that can least
 * afford it.
 *
 * WHICH tier applies is not a width threshold. The header watches its own
 * layout: after each render it asks whether the action group has dropped below
 * the title's row (`useHeaderTier`), and steps down one tier if it has. That
 * is the layout engine's own answer to "does it fit", so it holds for the
 * column's width rather than the window's — a narrow chat column in a wide
 * window is the ordinary case: with the workspace view open at 1280px the
 * column is 369px. It also holds for whatever the labels happen to be: the
 * labelled row is 500px in zh-TW and 521px in en (measured), and a threshold
 * constant would need re-measuring on every label or button change.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Icon, type IconName } from "../../components/Icon";
import { useContainerWidth } from "../../hooks/useContainerWidth";
import { pxToRem } from "../../lib/pxToRem";

export type HeaderTier = "labels" | "icons" | "menu";

export type HeaderAction = {
  id: string;
  testid: string;
  icon: IconName;
  label: string;
  /** The tooltip, and the accessible name in every tier. */
  tip: string;
  /** The accessible name, where it is not the label (kept as each button had it). */
  aria?: string;
  onClick: () => void;
};

/** The header action buttons (New chat / Tools / Skills / Workflows / Export).
 * `flexShrink: 0` + `whiteSpace: nowrap` keep each button intact so the wrapping
 * header drops a whole button to the next row instead of shrinking it and
 * letting its label wrap character-by-character at the narrow default width (#456). */
export const hdrBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  color: "var(--text-paper-d)",
  fontSize: pxToRem(11),
  background: "transparent",
  border: "none",
  cursor: "pointer",
  flexShrink: 0,
  whiteSpace: "nowrap",
};

/** The icon-only shape: a 28px hit target around a 16px glyph. 16px at stroke 2
 * is the size at which the seven can be told apart (P3's strip); 13px at 1.6,
 * the labelled size, cannot carry the row on its own. */
const hdrIconBtn: React.CSSProperties = {
  ...hdrBtn,
  width: 28,
  height: 28,
  justifyContent: "center",
  padding: 0,
  borderRadius: "var(--radius-btn)",
};

const ORDER: HeaderTier[] = ["labels", "icons", "menu"];
/** How much wider the header must grow, past the width at which a tier last
 * failed to fit, before that tier is tried again. Without it a header sitting
 * exactly at the edge would flip every resize event. */
const GROW_MARGIN = 24;

/**
 * Decide the tier by watching the header wrap.
 *
 * Returns the refs the header must attach: `headerRef` (its width triggers a
 * re-check), `identityRef` (the title block), `actionsRef` (the button group).
 * "Wrapped" means the group's top has reached the title block's bottom — with
 * `align-items: center` the two are never top-aligned on one row, so the
 * comparison is against the bottom, not the top.
 *
 * Steps DOWN one tier per wrapped render, recording the header width at which
 * the tier failed; steps UP only once the header is `GROW_MARGIN` wider than
 * that. A tier that is tried again and wraps again records the larger width,
 * so the search converges instead of oscillating.
 *
 * `forced` is the test seam: each tier's shape is pinned with it, and this
 * measuring is asserted in a real browser, where layout exists.
 */
export function useHeaderTier(forced?: HeaderTier) {
  const [observe, headerW] = useContainerWidth<HTMLElement>();
  const headerRef = useCallback((el: HTMLElement | null) => observe(el), [observe]);
  const identityRef = useRef<HTMLDivElement | null>(null);
  const actionsRef = useRef<HTMLDivElement | null>(null);
  const [measured, setMeasured] = useState<HeaderTier>("labels");
  const failedAt = useRef<Partial<Record<HeaderTier, number>>>({});

  useLayoutEffect(() => {
    if (forced) return;
    const identity = identityRef.current;
    const actions = actionsRef.current;
    if (!identity || !actions || headerW === 0) return;
    const title = identity.getBoundingClientRect();
    const group = actions.getBoundingClientRect();
    const wrapped = group.top >= title.bottom - 2;
    const i = ORDER.indexOf(measured);
    if (wrapped && i < ORDER.length - 1) {
      failedAt.current[measured] = headerW;
      setMeasured(ORDER[i + 1]);
      return;
    }
    if (!wrapped && i > 0) {
      const prev = ORDER[i - 1];
      const at = failedAt.current[prev];
      if (at === undefined || headerW > at + GROW_MARGIN) setMeasured(prev);
    }
  }, [forced, headerW, measured]);

  return { tier: forced ?? measured, headerRef, identityRef, actionsRef };
}

export function HeaderActions({
  tier,
  actions,
  groupRef,
}: {
  tier: HeaderTier;
  /** Falsy entries are actions whose condition did not hold; they are skipped. */
  actions: (HeaderAction | false | null | undefined)[];
  groupRef: React.MutableRefObject<HTMLDivElement | null>;
}) {
  const items = actions.filter((a): a is HeaderAction => !!a);
  return (
    <div
      ref={groupRef}
      data-testid="agent-header-actions"
      style={{ display: "flex", alignItems: "center", gap: tier === "icons" ? 4 : 10, flexShrink: 0 }}
    >
      {tier === "menu" ? (
        <HeaderMoreMenu items={items} />
      ) : (
        items.map((a) => (
          <button
            key={a.id}
            type="button"
            data-testid={a.testid}
            onClick={a.onClick}
            title={a.tip}
            aria-label={a.aria ?? a.label}
            style={tier === "icons" ? hdrIconBtn : hdrBtn}
          >
            {tier === "icons" ? (
              <Icon name={a.icon} size={16} strokeWidth={2} />
            ) : (
              <>
                <Icon name={a.icon} size={13} /> {a.label}
              </>
            )}
          </button>
        ))
      )}
    </div>
  );
}

/** One "⋯" and the actions behind it, with their labels — the same mechanics
 * as `WorkflowLaunchMenu` (click outside or Escape closes; picking closes). */
function HeaderMoreMenu({ items }: { items: HeaderAction[] }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="hdr-more" ref={rootRef}>
      <button
        type="button"
        data-testid="header-more-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More"
        title="More"
        onClick={() => setOpen((v) => !v)}
        style={hdrIconBtn}
      >
        <Icon name="dots_h" size={16} strokeWidth={2} />
      </button>
      {open && (
        <div role="menu" className="hdr-more__menu" data-testid="header-more-menu">
          {items.map((a) => (
            <button
              key={a.id}
              type="button"
              role="menuitem"
              className="hdr-more__item"
              data-testid={`header-more-${a.id}`}
              title={a.tip}
              onClick={() => {
                setOpen(false);
                a.onClick();
              }}
            >
              <Icon name={a.icon} size={14} />
              <span>{a.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
