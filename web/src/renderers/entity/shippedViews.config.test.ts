import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { load as parseYaml } from "js-yaml";
import { describe, expect, it } from "vitest";

import { parseViewSpec } from "./shared";
import { resolveViewRenderer } from "./viewKindRegistry";
import { VIEW_KIND } from "./types";

/**
 * A parse guard over EVERY shipped view file (#698).
 *
 * `parseViewSpec` now COERCES the platform's own fields, and coercion drops
 * what it doesn't recognise — silently, by design, because the alternative was
 * a crash. That trade is only safe while the values these files actually ship
 * survive it. Until now only `gantt.ai.yaml`'s `week:` block was pinned
 * (`pmGanttWeek.config.test.ts`); `roadmap.ai.yaml` carries the same block and
 * nothing would have gone red if a future coercion rule dropped it.
 *
 * Reading the real directory rather than a hardcoded list means a NEW shipped
 * view is covered the day it lands, without anyone remembering to add it here.
 */
const VIEWS_DIR = resolve(process.cwd(), "../src/workspace_app/apps/pm/profiles/default/views");
const files = readdirSync(VIEWS_DIR).filter((f) => f.endsWith(".ai.yaml"));

describe("every shipped PM view file survives the parser", () => {
  it("finds the shipped views (so this suite can't pass by scanning nothing)", () => {
    // Five since #785 retired `workload.ai.yaml` — it was `gantt.ai.yaml` with
    // one key changed, and `group_by` is a setting the gear panel edits in
    // place. A floor, not a count: a new view raises it, and only a scan that
    // found nothing (the failure this guard exists for) drops below it.
    expect(files.length).toBeGreaterThanOrEqual(5);
  });

  for (const file of files) {
    describe(file, () => {
      const spec = parseViewSpec(readFileSync(resolve(VIEWS_DIR, file), "utf8"));

      it("parses as a view", () => {
        expect(spec).not.toBeNull();
      });

      it("names a kind the app can actually render", () => {
        // `health` is intercepted by the container ahead of the dispatcher, so
        // it is deliberately not a registry entry.
        if (spec!.view === VIEW_KIND.health) return;
        expect(resolveViewRenderer(spec!.view).kind).toBe(spec!.view);
      });

      it("keeps an `entity:` if the kind needs one", () => {
        const renderer = resolveViewRenderer(spec!.view);
        if (renderer.needsEntity) expect(spec!.entity).toBeTruthy();
      });

      // Key by key, not block by block. A coarser "the block still exists"
      // check passes while coercion quietly eats ONE value inside it — which is
      // exactly the mistake this branch already made once, dropping `by_today`
      // from an enum list hand-written against a type in another file.
      it("keeps every value it actually ships", () => {
        const raw = parseYaml(readFileSync(resolve(VIEWS_DIR, file), "utf8")) as Record<string, unknown>;
        const lost: string[] = [];
        const check = (declared: unknown, parsed: unknown, path: string): void => {
          if (declared === null || declared === undefined) return; // not shipping a value
          if (parsed === undefined) lost.push(path);
        };
        for (const [k, v] of Object.entries(raw)) {
          if (v && typeof v === "object" && !Array.isArray(v)) {
            const parsedBlock = (spec as unknown as Record<string, unknown>)[k];
            for (const [ik, iv] of Object.entries(v as Record<string, unknown>)) {
              check(iv, (parsedBlock as Record<string, unknown> | undefined)?.[ik], `${k}.${ik}`);
            }
          } else {
            check(v, (spec as unknown as Record<string, unknown>)[k], k);
          }
        }
        expect(lost, `coercion dropped values this file ships`).toEqual([]);
      });
    });
  }
});
