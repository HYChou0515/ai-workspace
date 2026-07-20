import { describe, expect, it, vi } from "vitest";

import { HttpError } from "./http";
import { writeVerified } from "./writeVerified";

/**
 * One definition of "did this write succeed", for every surface that writes a
 * workspace file.
 *
 * The honest criterion is "are the bytes there", but each call site was using
 * "did the HTTP response come back OK" — and those differ precisely when the
 * connection is cut AFTER the body has been sent, which is when the server has
 * usually stored the file already. Every upload surface (the file tree, the
 * composer's attachments, the skills / workflows / collections pickers, the
 * editor's save, both KB IDEs) had that exposure, and fixing them one at a time
 * is how the criterion ends up different in each.
 */
describe("writeVerified", () => {
  it("passes a successful write straight through", async () => {
    const exists = vi.fn(async () => true);
    await writeVerified(
      async () => {},
      exists,
    );
    expect(exists).not.toHaveBeenCalled(); // no need to ask
  });

  it("treats a cut connection as success when the bytes are there", async () => {
    await expect(
      writeVerified(
        async () => {
          throw new HttpError(0, "network error");
        },
        async () => true,
      ),
    ).resolves.toBeUndefined();
  });

  it("still fails when a cut connection really did lose the write", async () => {
    await expect(
      writeVerified(
        async () => {
          throw new HttpError(504, "gateway timeout");
        },
        async () => false,
      ),
    ).rejects.toMatchObject({ status: 504 });
  });

  // A refusal is an answer, not a question. Asking again would only be slower,
  // and could mistake a file left over from an earlier attempt for a success.
  it("never second-guesses a definite refusal", async () => {
    const exists = vi.fn(async () => true);
    await expect(
      writeVerified(async () => {
        throw new HttpError(413, "too large");
      }, exists),
    ).rejects.toMatchObject({ status: 413 });
    expect(exists).not.toHaveBeenCalled();
  });

  it("reports the original failure when it cannot check", async () => {
    // Losing the ability to look is not evidence that the write worked.
    await expect(
      writeVerified(
        async () => {
          throw new HttpError(502, "bad gateway");
        },
        async () => {
          throw new Error("list failed too");
        },
      ),
    ).rejects.toMatchObject({ status: 502 });
  });

  it("does not verify an error that carries no status at all", async () => {
    // A statusless error is a client-side bug (a bad argument, a thrown string),
    // not an interrupted request.
    const exists = vi.fn(async () => true);
    await expect(
      writeVerified(async () => {
        throw new Error("boom");
      }, exists),
    ).rejects.toThrow("boom");
    expect(exists).not.toHaveBeenCalled();
  });
});
