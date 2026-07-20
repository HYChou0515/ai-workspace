/**
 * One definition of "did this write succeed", for every surface that writes a
 * workspace file.
 *
 * The honest criterion is *are the bytes there*. Each call site was using *did
 * the HTTP response come back OK*, and the two differ in exactly one case: the
 * connection is cut AFTER the body has been sent, which is when the server has
 * usually stored the file already. The user is then told their upload failed
 * about a file sitting right there in the tree — and, because each surface
 * invented its own wording, told a different (often invented) reason each time.
 *
 * Every writer has that exposure: the file tree, the composer's attachments, the
 * skills / workflows / collections pickers, the editor's save, both KB IDEs.
 * Fixing them one at a time is how the criterion ends up different in each — so
 * this lives at the service boundary instead, where a call site cannot get it
 * wrong by omission.
 */

/** Statuses that mean "the request was cut", not "the write was refused". The
 * body may already have reached the server, so the outcome is unknown until we
 * look. `0` is a bare network drop (the XHR/fetch error path). */
const INCONCLUSIVE = new Set([0, 502, 503, 504]);

/** Whether `err` leaves the outcome of a write genuinely unknown. An error with
 * no status at all is a client-side bug (a bad argument, a thrown string), not
 * an interrupted request, so it is never treated as inconclusive. */
export function isInconclusive(err: unknown): boolean {
  const status = (err as { status?: number } | null)?.status;
  return status !== undefined && INCONCLUSIVE.has(status);
}

/**
 * Run `write`; if it fails inconclusively, ask `exists` whether the write landed
 * anyway and succeed if it did.
 *
 * A definite refusal (413 too large, 507 out of space, 403 forbidden) is an
 * answer, not a question — it is re-thrown untouched. Asking again would only be
 * slower and could mistake a file left over from an earlier attempt for a
 * success. Losing the ability to look is likewise not evidence that the write
 * worked, so a failing `exists` re-throws the original error.
 */
export async function writeVerified(
  write: () => Promise<void>,
  exists: () => Promise<boolean>,
): Promise<void> {
  try {
    await write();
  } catch (err) {
    if (!isInconclusive(err)) throw err;
    if (await exists().catch(() => false)) return;
    throw err;
  }
}
