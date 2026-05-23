/**
 * Pure reducer over CellEvent stream. Folds incoming events into the
 * cell's `outputs` array + execution_count + duration, so the UI just
 * re-renders the cell on every update.
 */

import type { CellEvent } from "../../events";
import type { NbCell, NbOutput } from "./types";

export type CellRunState = {
  outputs: NbOutput[];
  status: "running" | "ok" | "error" | "idle";
  execution_count: number | null;
  startedAt: number | null;
  durationMs: number | null;
};

export function startRun(now = Date.now()): CellRunState {
  return {
    outputs: [],
    status: "running",
    execution_count: null,
    startedAt: now,
    durationMs: null,
  };
}

export function reduceCellEvent(
  state: CellRunState,
  ev: CellEvent,
  now: number = Date.now(),
): CellRunState {
  switch (ev.type) {
    case "cell_stream": {
      // Coalesce consecutive stream events of the same name to keep the
      // outputs list small. Mirrors what jupyter does.
      const last = state.outputs[state.outputs.length - 1];
      if (last && last.output_type === "stream" && last.name === ev.stream) {
        const text = (Array.isArray(last.text) ? last.text.join("") : last.text) + ev.text;
        return {
          ...state,
          outputs: [
            ...state.outputs.slice(0, -1),
            { output_type: "stream", name: ev.stream, text },
          ],
        };
      }
      return {
        ...state,
        outputs: [
          ...state.outputs,
          { output_type: "stream", name: ev.stream, text: ev.text },
        ],
      };
    }
    case "cell_display_data":
      return {
        ...state,
        outputs: [...state.outputs, { output_type: "display_data", data: ev.data }],
      };
    case "cell_error":
      return {
        ...state,
        status: "error",
        outputs: [
          ...state.outputs,
          {
            output_type: "error",
            ename: ev.ename,
            evalue: ev.evalue,
            traceback: ev.traceback,
          },
        ],
      };
    case "cell_done":
      return {
        ...state,
        status: state.status === "error" ? "error" : "ok",
        execution_count: ev.execution_count,
        durationMs: state.startedAt != null ? now - state.startedAt : null,
      };
  }
}

/** Merge run state back into the underlying NbCell for persistence. */
export function mergeIntoCell(cell: NbCell, run: CellRunState): NbCell {
  if (cell.cell_type !== "code") return cell;
  return {
    ...cell,
    outputs: run.outputs,
    execution_count: run.execution_count ?? cell.execution_count ?? null,
  };
}
