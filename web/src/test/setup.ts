import { beforeEach, vi } from "vitest";

/**
 * No test may open a socket.
 *
 * happy-dom gives a test document the location `http://localhost:3000/`, so an
 * unmocked `fetch("/api/…")` is not an inert no-op — it is a real connection
 * attempt to a port nothing is listening on. The suite makes hundreds of them,
 * and each one's rejection lands whenever the event loop gets to it, which is
 * often *after* the test that started it has finished. Vitest counts a
 * rejection with no owner as an unhandled error and exits 1 — so CI fails with
 * every single test passing, and only sometimes, depending on whether the last
 * straggler beat the reporter to the finish line.
 *
 * Rejecting here keeps what a test can observe identical (a request it awaits
 * still fails) while removing the socket, the timing and the race. The message
 * names the fix, because the failure it replaces was famously unhelpful.
 *
 * Re-installed before EVERY test, not once per file: several suites end with
 * `vi.unstubAllGlobals()`, which would otherwise strip this guard after their
 * first test and quietly let the rest of the file back onto the network.
 * A test that stubs `fetch` itself still wins — a setup file's hooks run
 * before the test file's.
 */
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.reject(
        new Error(
          "fetch is not stubbed in this test — mock the request (or the module " +
            "that makes it) instead of letting it reach the network",
        ),
      ),
    ),
  );
});
