// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { EntityViewProps } from "./types";
import { GanttView } from "./GanttView";
import {
  DARK,
  LIGHT,
  TOKENS_CSS,
  contrast,
  isDeclared,
  mixSrgb,
  over,
  paintedOver,
  parseFill,
  tokenIn,
} from "../../test/contrast";
import { ENTITY_VIEWS_CSS, effective, inheritedColor } from "../../test/cssRules";

/**
 * The text on a gantt bar has to be readable in BOTH themes.
 *
 * #690 gave the bar a `color_by` fill — `selectColor(...).bg`, a chip fill —
 * but left the ink the bar wore when it was a solid blue slab. The two halves
 * of that palette pair come from different files (the fill is an inline style
 * in GanttView.tsx, the ink is a rule in entity-views.css), so nothing in the
 * suite ever compared them: every existing assertion checks that the fill
 * STRING matches selectColor, which it did, while the label rendered cream on
 * a 93%-white fill at 1.07:1 and was invisible in light mode.
 *
 * So this guard resolves the pairing the way a browser would — inline style
 * first, then the cascade — and takes the actual ratio. The floor is the 3:1
 * the chip palette is already held to in styles/contrast.test.ts: a bar label
 * is the same thing as a chip (`--text-xs`, weight 600, short) on the same
 * fill, and holding it to a different number would mean the design system
 * promises two things about one pairing.
 */

afterEach(cleanup);

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "span", role: "daterange" },
    { name: "assignee", role: "actor" },
    // named by the `schedule:` block a provisional bar needs to exist at all
    { name: "exp_days", role: "number" },
    { name: "schedule", role: "status", values: ["auto", "manual"] },
    {
      name: "urgency",
      role: "status",
      values: ["critical", "high", "medium", "low"],
      colors: { critical: "red", high: "amber", medium: "blue", low: "slate" },
    },
  ],
  form: [],
};
const users = [{ id: "alice", name: "Alice Chen", section: "", email: "", photo_url: "" }];
const SPAN = "2026-01-10/2026-01-20";

const rec = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});

function props(overrides: Partial<EntityViewProps> = {}): EntityViewProps {
  return {
    spec: { view: "gantt", entity: "issue", span: "span", label: "title" },
    type,
    entities: [],
    onCreate: vi.fn(),
    onPatch: vi.fn(),
    ...overrides,
  };
}

/**
 * .ev-gantt__lane-band: color-mix(in srgb, var(--paper-2) 60%, transparent)
 * over the chart surface — i.e. --paper-2 at 60% alpha. Shared by both extent
 * guards below, since "a coloured bar" now means two different palettes.
 */
const laneBand = (block: RegExp) => {
  const [r, g, b] = parseFill(tokenIn(TOKENS_CSS, block, "--paper-2"));
  return over([r, g, b, 0.6], tokenIn(TOKENS_CSS, block, "--white"));
};

const THEMES = [
  ["light", LIGHT],
  ["dark", DARK],
] as const;

/**
 * The opaque colour a resolved ink value denotes.
 *
 * `var(--a, <fallback>)` is resolved the way a browser would when nothing sets
 * `--a`: some of these values name a custom property published at RUNTIME (the
 * bar's own `--bar-ink`), which by definition is not declared in tokens.css, so
 * on an element that does not set it the FALLBACK is what paints.
 */
function inkHex(value: string, block: RegExp): string {
  const v = value.trim();
  const ref = /^var\(\s*(--[\w-]+)\s*(?:,([\s\S]+))?\)$/.exec(v);
  if (!ref) return v;
  // ONLY an undeclared property falls through to the fallback. A declared token
  // that is not a hex colour must still raise: swallowing that too would mean a
  // token re-tuned to `color-mix()` silently gets measured as its fallback, and
  // the guard keeps passing over a colour it never looked at.
  if (isDeclared(TOKENS_CSS, block, ref[1])) return tokenIn(TOKENS_CSS, block, ref[1]);
  if (ref[2] === undefined) throw new Error(`${ref[1]} is neither declared nor given a fallback`);
  return inkHex(ref[2], block);
}

/**
 * The ink an element inside the bar actually paints in.
 *
 * Order matters and is easy to get backwards: an element's OWN declaration
 * beats anything it would otherwise inherit, so a `color` rule on the label
 * wins over an inline `color` on the bar. Checking the bar's inline style
 * first made this guard unable to see a label that pins its own ink — which
 * is the entire defect — so it stays written in cascade order.
 *
 * `chain[0]` is the element's own selector; the rest are its ancestors.
 */
function resolvedInk(el: HTMLElement, bar: HTMLElement, chain: string[]): string {
  if (el.style.color) return el.style.color; // inline on the element itself
  const own = effective(ENTITY_VIEWS_CSS, chain[0], "color");
  if (own && own !== "inherit") return own; // the element's own rule
  if (bar.style.color) return bar.style.color; // inherited: inline on the bar
  return inheritedColor(ENTITY_VIEWS_CSS, chain.slice(1)) || ""; // inherited: from CSS
}

/**
 * The ink the avatar's ring actually paints in.
 *
 * The RULE is the source and the element's custom properties only fill its
 * variable in — that order matters, and getting it backwards is how a guard
 * stops guarding: reading the bar's inline `--bar-ink` first short-circuits
 * before the stylesheet is ever consulted, so the assertion re-measures the
 * palette against itself and passes no matter what `border-color` says. Written
 * this way, dropping either half — the rule's `var(--bar-ink, …)` or the inline
 * value GanttView publishes — falls back to the cream ink and fails.
 */
function ringInk(bar: HTMLElement): string {
  const rule = effective(ENTITY_VIEWS_CSS, ".ev-gantt__bar-avatar", "border-color") ?? "";
  const named = /var\(\s*(--[\w-]+)/.exec(rule);
  return (named && bar.style.getPropertyValue(named[1])) || rule;
}

/** Render one bar and hand back the element plus its label / assignee. */
function renderBar(fields: Record<string, unknown>, spec: Partial<EntityViewProps["spec"]> = {}) {
  render(
    createElement(
      GanttView,
      props({
        spec: {
          view: "gantt",
          entity: "issue",
          span: "span",
          label: "title",
          assignee: "assignee",
          assignee_display: "name",
          ...spec,
        } as EntityViewProps["spec"],
        entities: [rec(1, { title: "A task", span: SPAN, ...fields })],
        users,
      }),
    ),
  );
  const bar = screen.getByTestId("bar-1");
  return {
    bar,
    // Looked up lazily: a record with no assignee renders no such node, and an
    // eager query would fail the LABEL tests for a reason that is not theirs.
    label: () => bar.querySelector(".ev-gantt__bar-label") as HTMLElement,
    assignee: () => screen.getByTestId("bar-1-assignee") as HTMLElement,
  };
}

describe("a coloured gantt bar's text (#690)", () => {
  for (const urgency of ["critical", "high", "medium", "low"]) {
    for (const [themeName, block] of THEMES) {
      it(`keeps the ${urgency} label ≥3:1 on its own fill in ${themeName} mode`, () => {
        const { bar, label } = renderBar({ urgency }, { color_by: "urgency" });

        const surface = tokenIn(TOKENS_CSS, block, "--white"); // .ev-gantt__scroll
        const fill = paintedOver(TOKENS_CSS, block, bar.style.background, surface);
        const ink = resolvedInk(label(), bar, [".ev-gantt__bar-label", ".ev-gantt__bar"]);
        expect(ink, "the label paints in no colour at all").not.toBe("");

        const ratio = contrast(inkHex(ink, block), fill);
        expect(
          ratio,
          `${urgency} label in ${themeName}: ${ink} on ${fill} = ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      });

      it(`keeps the ${urgency} assignee name ≥3:1 on its own fill in ${themeName} mode`, () => {
        const { bar, assignee } = renderBar({ urgency, assignee: "alice" }, { color_by: "urgency" });

        const surface = tokenIn(TOKENS_CSS, block, "--white");
        const fill = paintedOver(TOKENS_CSS, block, bar.style.background, surface);
        const ink = resolvedInk(assignee(), bar, [".ev-gantt__bar-assignee-name", ".ev-gantt__bar"]);

        const ratio = contrast(inkHex(ink, block), fill);
        expect(
          ratio,
          `${urgency} assignee in ${themeName}: ${ink} on ${fill} = ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      });
    }
  }
});

/**
 * `color_by` on an ACTOR field draws its fills from a generated palette
 * (`actorColor`) rather than the six chip slots, so the pairing the guard above
 * checks for `status` has to be re-checked here: those fills are OPAQUE oklch
 * rather than translucent chip fills, and there are as many of them as there
 * are people, not six.
 *
 * The floor is the same 3:1 — one pairing, one promise — and the team is walked
 * up to a size no chip palette could have served, because "readable" has to
 * hold for the sixteenth person, not just the first.
 */
describe("a gantt bar coloured by ACTOR", () => {
  const TEAM = 16;

  /** One bar per person, so every seat in the palette is actually painted. */
  function renderTeam(extra: Partial<EntityViewProps["spec"]> = {}) {
    render(
      createElement(
        GanttView,
        props({
          spec: {
            view: "gantt",
            entity: "issue",
            span: "span",
            label: "title",
            assignee: "assignee",
            assignee_display: "name",
            color_by: "assignee",
            ...extra,
          } as EntityViewProps["spec"],
          entities: Array.from({ length: TEAM }, (_, i) =>
            rec(i + 1, { title: `Task ${i + 1}`, span: SPAN, assignee: `u${i}` }),
          ),
          users: Array.from({ length: TEAM }, (_, i) => ({
            id: `u${i}`, name: `Person ${i}`, section: "", email: "", photo_url: "",
          })),
        }),
      ),
    );
    return Array.from({ length: TEAM }, (_, i) => screen.getByTestId(`bar-${i + 1}`));
  }

  for (const [themeName, block] of THEMES) {
    it(`keeps every one of ${TEAM} people's labels ≥3:1 on their own fill in ${themeName} mode`, () => {
      const surface = tokenIn(TOKENS_CSS, block, "--white");
      for (const [i, bar] of renderTeam().entries()) {
        const label = bar.querySelector(".ev-gantt__bar-label") as HTMLElement;
        const fill = paintedOver(TOKENS_CSS, block, bar.style.background, surface);
        const ink = resolvedInk(label, bar, [".ev-gantt__bar-label", ".ev-gantt__bar"]);
        const ratio = contrast(inkHex(ink, block), fill);
        expect(
          ratio,
          `person ${i} in ${themeName}: ${ink} on ${fill} = ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      }
    });
  }

  it("paints no two of them the same colour", () => {
    const fills = new Set(renderTeam().map((b) => b.style.background));
    expect(fills.size).toBe(TEAM);
  });

  /**
   * The property a gantt actually owes: you can see where a bar starts and
   * ends. Two things can carry it — the fill standing off the lane band, or the
   * edge — and which one does depends on the theme, because an actor fill is
   * OPAQUE and light where a chip fill is translucent:
   *
   *   light mode — fill 2.4:1 (too close to cream), edge `--ink` 15:1  → edge
   *   dark mode  — edge `--ink` 1.1:1 (it IS the dark), fill 5.9:1     → fill
   *
   * Asking for the edge alone would demand a solution to a problem the dark
   * theme does not have; asking for the fill alone would miss light mode. So
   * the guard asks for the boundary, and names which half is carrying it — a
   * regression that flips or flattens either one still fails here, and #690's
   * original state (fill ≈ band, edge transparent) fails on both halves.
   */
  for (const [themeName, block] of THEMES) {
    it(`makes every person's bar state its extent in ${themeName} mode`, () => {
      const band = laneBand(block);
      const surface = tokenIn(TOKENS_CSS, block, "--white");
      for (const [i, bar] of renderTeam({ group_by: "assignee" }).entries()) {
        const edge = bar.style.borderColor || effective(ENTITY_VIEWS_CSS, ".ev-gantt__bar", "border");
        expect(edge, "the bar draws no boundary of its own").toBeTruthy();
        expect(edge, "a transparent edge is not an edge").not.toContain("transparent");

        const byEdge = contrast(inkHex(edge as string, block), band);
        const byFill = contrast(paintedOver(TOKENS_CSS, block, bar.style.background, surface), band);
        expect(
          Math.max(byEdge, byFill),
          `person ${i} in ${themeName}: edge ${byEdge.toFixed(2)}:1, fill ${byFill.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      }
    });
  }

  /**
   * The avatar's ring is the only part of the bar's furniture that touches the
   * FILL rather than sitting on the avatar's own disc, so it has to contrast
   * with whatever the bar is wearing. P5 pinned it to `--text-dark` — right
   * while every bar was a dark blue slab, wrong the moment a bar can be light,
   * which is what an actor fill is. The guard for it only ever rendered the
   * default bar, so the hole is exactly where this change lands.
   */
  for (const [themeName, block] of THEMES) {
    it(`keeps the avatar's ring readable on a person-coloured bar in ${themeName} mode`, () => {
      const surface = tokenIn(TOKENS_CSS, block, "--white");
      for (const [i, bar] of renderTeam({ assignee_display: "avatar" }).entries()) {
        const avatar = bar.querySelector(".ev-gantt__bar-avatar") as HTMLElement;
        const ring = ringInk(bar);
        expect(avatar, "the bar renders no avatar to ring").toBeTruthy();

        const fill = paintedOver(TOKENS_CSS, block, bar.style.background, surface);
        const ratio = contrast(inkHex(ring, block), fill);
        expect(
          ratio,
          `person ${i}'s ring in ${themeName}: ${ring} on ${fill} = ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      }
    });
  }

  it("carries the extent by the EDGE in light mode and by the FILL in dark", () => {
    // Named rather than implied: this is the pairing that makes the guard above
    // pass, and if it ever inverts, the boundary has moved to a mechanism
    // nobody checked.
    const bars = renderTeam({ group_by: "assignee" });
    const surface = (block: RegExp) => tokenIn(TOKENS_CSS, block, "--white");
    const fillOf = (bar: HTMLElement, block: RegExp) =>
      contrast(paintedOver(TOKENS_CSS, block, bar.style.background, surface(block)), laneBand(block));
    const edgeOf = (bar: HTMLElement, block: RegExp) =>
      contrast(inkHex(bar.style.borderColor, block), laneBand(block));

    expect(edgeOf(bars[0], LIGHT)).toBeGreaterThanOrEqual(3);
    expect(fillOf(bars[0], LIGHT)).toBeLessThan(3);
    expect(fillOf(bars[0], DARK)).toBeGreaterThanOrEqual(3);
    expect(edgeOf(bars[0], DARK)).toBeLessThan(3);
  });
});

/**
 * A provisional bar is the schedule's GUESS for work nobody has estimated. Its
 * rule makes it hollow and dashed so it is, in that rule's own words, "never
 * mistaken for the solid bars around it".
 *
 * But that rule lives in the stylesheet and the colour is an inline style, and
 * inline wins — so on any `color_by` chart the guess paints as a solid, fully
 * planned bar. Pre-existing since #690 P2, and invisible until now because
 * every other guard renders bars that are not provisional.
 */
describe("a provisional bar on a coloured chart", () => {
  const withSchedule = (colorBy: string) => ({
    color_by: colorBy,
    schedule: { span: "span", duration: "exp_days", flag: "schedule" },
  });

  for (const [field, value] of [
    ["urgency", "critical"],
    ["assignee", "alice"],
  ] as const) {
    it(`keeps the guess hollow when the chart is coloured by ${field}`, () => {
      const { bar } = renderBar({ [field]: value }, withSchedule(field));

      expect(bar.dataset.provisional, "the row is not provisional to begin with").toBe("true");
      // All three inline paints have to stay off, or the stylesheet's hollow
      // rule loses to them one by one: it sets a transparent fill, its own ink
      // and a dashed edge, and an inline value beats each of those separately.
      expect(bar.style.background, "a guess wearing a solid fill").toBe("");
      expect(bar.style.color, "a guess wearing the fill's ink").toBe("");
      expect(bar.style.borderColor, "a guess wearing a solid edge").toBe("");
    });
  }

  it("still colours the same record once it has an estimate", () => {
    const { bar } = renderBar({ assignee: "alice", exp_days: 3 }, withSchedule("assignee"));
    expect(bar.dataset.provisional).toBeUndefined();
    expect(bar.style.background).not.toBe("");
  });
});

describe("a coloured gantt bar's extent", () => {
  // A chip fill is translucent, and in a GROUPED chart it lands on the lane
  // band — which for the neutral slot is literally the same token. The bar
  // then has no readable edge, and in a gantt the bar's start and end ARE the
  // data. The solid fill used to do this job; since #690 it cannot, so the
  // bar states its own boundary in the ink it already carries.
  for (const urgency of ["critical", "low"]) {
    for (const [themeName, block] of THEMES) {
      it(`gives the ${urgency} bar an edge against a lane band in ${themeName} mode`, () => {
        const { bar } = renderBar({ urgency }, { color_by: "urgency", group_by: "urgency" });

        const edge = bar.style.borderColor || effective(ENTITY_VIEWS_CSS, ".ev-gantt__bar", "border");
        expect(edge, "the bar draws no boundary of its own").toBeTruthy();
        expect(edge, "a transparent edge is not an edge").not.toContain("transparent");

        const ratio = contrast(inkHex(edge as string, block), laneBand(block));
        expect(
          ratio,
          `${urgency} bar edge in ${themeName}: ${edge} on ${laneBand(block)} = ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      });
    }
  }

  it("keeps the uncoloured bar's box metrics identical to a coloured one", () => {
    // The edge must not make coloured bars a different SIZE — a border that
    // changes the box would shift every bar's start by a pixel depending on
    // whether the view happens to colour it.
    const border = effective(ENTITY_VIEWS_CSS, ".ev-gantt__bar", "border");
    expect(border, ".ev-gantt__bar declares no border to reserve the space").toBeTruthy();
    expect(border).toMatch(/^1px solid /);
  });
});

describe("an uncoloured gantt bar's text (the default blue slab)", () => {
  // No `color_by`, so the bar keeps its CSS gradient and there is no palette
  // ink to inherit. `--white` used to serve here — a SURFACE token standing in
  // for a foreground, which inverts with the theme and put near-black text on
  // the blue bar at 2.88:1 in dark mode. Both ends of the gradient count: the
  // ink has to survive the lightest AND the darkest point of the fill.
  const gradientEnds = (block: RegExp) => {
    const info = tokenIn(TOKENS_CSS, block, "--info");
    const ink = tokenIn(TOKENS_CSS, block, "--ink");
    return { top: info, bottom: mixSrgb(info, ink, 0.22) };
  };

  for (const [themeName, block] of THEMES) {
    for (const part of ["label", "assignee"] as const) {
      it(`keeps the ${part} ≥3:1 at both ends of the gradient in ${themeName} mode`, () => {
        const rendered = renderBar({ assignee: "alice" });
        const el = rendered[part]();
        const chain =
          part === "label"
            ? [".ev-gantt__bar-label", ".ev-gantt__bar"]
            : [".ev-gantt__bar-assignee-name", ".ev-gantt__bar"];
        const ink = inkHex(resolvedInk(el, rendered.bar, chain), block);

        const { top, bottom } = gradientEnds(block);
        for (const [end, fill] of [
          ["top", top],
          ["bottom", bottom],
        ] as const) {
          const ratio = contrast(ink, fill);
          expect(
            ratio,
            `${part} in ${themeName} at gradient ${end}: ${ink} on ${fill} = ${ratio.toFixed(2)}:1`,
          ).toBeGreaterThanOrEqual(3);
        }
      });
    }
  }

  for (const [themeName, block] of THEMES) {
    it(`keeps a provisional (hollow) bar's label readable in ${themeName} mode`, () => {
      // `[data-provisional]` is `background: transparent` + its own
      // `color: var(--text-paper)` — a rule already written as though the bar
      // carried the ink. The label used to pin `--text-dark` and win, so a
      // hollow bar wrote cream on the white canvas: the same defect, on the
      // one bar whose fill IS the canvas.
      const ink = inheritedColor(ENTITY_VIEWS_CSS, [
        ".ev-gantt__bar-label",
        ".ev-gantt__bar[data-provisional]",
        ".ev-gantt__bar",
      ]);
      expect(ink, "a provisional bar's label paints in no colour at all").toBeTruthy();

      const canvas = tokenIn(TOKENS_CSS, block, "--white");
      const ratio = contrast(inkHex(ink as string, block), canvas);
      expect(
        ratio,
        `provisional label in ${themeName}: ${ink} on ${canvas} = ${ratio.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(3);
    });
  }

  for (const [themeName, block] of THEMES) {
    it(`keeps the avatar's ring readable on the default bar in ${themeName} mode`, () => {
      // Decorative, but it was the clearest symptom: a ring in `--white` is a
      // white ring in light and a near-black one in dark, on the SAME blue bar.
      // Note it cannot be `currentColor` — .ev-avatar sets its own colour for
      // the initials, so currentColor would pick that up instead of the ink.
      const ring = effective(ENTITY_VIEWS_CSS, ".ev-gantt__bar-avatar", "border-color");
      expect(ring, "the avatar draws no ring").toBeTruthy();
      expect(ring, "a ring in currentColor picks up the avatar's own accent").not.toBe(
        "currentColor",
      );

      const info = tokenIn(TOKENS_CSS, block, "--info");
      const darkest = mixSrgb(info, tokenIn(TOKENS_CSS, block, "--ink"), 0.22);
      for (const [end, fill] of [
        ["top", info],
        ["bottom", darkest],
      ] as const) {
        const ratio = contrast(inkHex(ring as string, block), fill);
        expect(
          ratio,
          `avatar ring in ${themeName} at gradient ${end}: ${ring} on ${fill} = ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(3);
      }
    });
  }

  it("never dresses bar text in a surface token", () => {
    // The defect in one line: --white is a SURFACE. Reaching for it as a
    // foreground is what made the ink flip the wrong way with the theme, and
    // the neighbouring label rule already knew to use --text-dark instead.
    for (const selector of [
      ".ev-gantt__bar",
      ".ev-gantt__bar-label",
      ".ev-gantt__bar-assignee-name",
    ]) {
      const color = effective(ENTITY_VIEWS_CSS, selector, "color");
      expect(color ?? "", `${selector} paints text in a surface token`).not.toMatch(
        /var\(--(white|paper|paper-2|paper-3)\)/,
      );
    }
  });
});
