/**
 * useEntityWrite (#448 P1/P2) — the single entity write seam every renderer
 * rides (table inline, board drag, quick-create, the file editor). It layers the
 * optimistic-lock + conflict contract onto the shared `update`/`create` path:
 *
 *  - **Optimistic** (§B1): a patch reflects in the cached list immediately, then
 *    confirms; any failure rolls back and refetches.
 *  - **Conflict** (§B2): a patch echoes the record's `version` as
 *    `expected_version`; a 409 (`EntityConflictError`) does NOT clobber — it
 *    surfaces the record number in `conflicts` and reloads so the row shows the
 *    other person's value. The caller renders a non-blocking banner + dismiss.
 *  - **`canWrite`** (§E): false makes every write a no-op so a read-only member's
 *    UI can hide its write affordances centrally, without each renderer knowing.
 *  - **`invalidate`** is the SSE seam (§C3/§E): an external-change handler calls
 *    it to pull collaborators' edits into the open view.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { EntityConflictError, entitiesApi, type EntityInstance, type EntityList } from "../api/entities";
import { qk } from "../api/queryKeys";

export type UseEntityWriteOptions = {
  /** When false the surface is read-only (non-member, §E): every write is a
   * no-op and callers hide their write affordances. Default true. */
  canWrite?: boolean;
};

type UpdateVars = { number: number; patch: Record<string, unknown>; expectedVersion?: string };

export function useEntityWrite(slug: string, itemId: string, type: string, options?: UseEntityWriteOptions) {
  const canWrite = options?.canWrite ?? true;
  const qc = useQueryClient();
  const [conflicts, setConflicts] = useState<number[]>([]);

  const invalidate = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.entities.list(slug, itemId, type) });
  }, [qc, slug, itemId, type]);

  const create = useMutation<EntityInstance, Error, Record<string, unknown>>({
    mutationFn: (args) => entitiesApi.create(slug, itemId, type, args),
    onSettled: invalidate,
  });

  const update = useMutation<EntityInstance, Error, UpdateVars, { prev?: EntityList }>({
    onMutate: async ({ number, patch }) => {
      const listKey = qk.entities.list(slug, itemId, type);
      await qc.cancelQueries({ queryKey: listKey });
      const prev = qc.getQueryData<EntityList>(listKey);
      if (prev) {
        qc.setQueryData<EntityList>(listKey, {
          ...prev,
          entities: prev.entities.map((e) =>
            e.number === number ? { ...e, fields: { ...e.fields, ...patch } } : e,
          ),
        });
      }
      return { prev };
    },
    mutationFn: ({ number, patch, expectedVersion }) =>
      // Omit the optional arg entirely when there's no version (last-write),
      // keeping the call arity clean for callers/tests.
      expectedVersion === undefined
        ? entitiesApi.update(slug, itemId, type, number, patch)
        : entitiesApi.update(slug, itemId, type, number, patch, expectedVersion),
    onError: (err, { number }, ctx) => {
      // Roll back the optimistic edit; a real conflict then reloads (onSettled).
      if (ctx?.prev) qc.setQueryData(qk.entities.list(slug, itemId, type), ctx.prev);
      if (err instanceof EntityConflictError) {
        setConflicts((cs) => (cs.includes(number) ? cs : [...cs, number]));
      }
    },
    onSuccess: (_data, { number }) => {
      setConflicts((cs) => cs.filter((n) => n !== number));
    },
    onSettled: invalidate,
  });

  const patch = useCallback(
    (number: number, patchObj: Record<string, unknown>) => {
      if (!canWrite) return;
      const list = qc.getQueryData<EntityList>(qk.entities.list(slug, itemId, type));
      const expectedVersion = list?.entities.find((e) => e.number === number)?.version;
      update.mutate({ number, patch: patchObj, expectedVersion });
    },
    [canWrite, qc, slug, itemId, type, update],
  );

  const createRecord = useCallback(
    (args: Record<string, unknown>) => {
      if (!canWrite) return;
      create.mutate(args);
    },
    [canWrite, create],
  );

  const dismissConflict = useCallback((number: number) => {
    setConflicts((cs) => cs.filter((n) => n !== number));
  }, []);

  return {
    canWrite,
    create: createRecord,
    patch,
    isBusy: create.isPending || update.isPending,
    conflicts,
    dismissConflict,
    /** SSE seam (§C3/§E) — pull external changes into the open view. */
    invalidate,
  };
}
