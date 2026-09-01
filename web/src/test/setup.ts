import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

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

/**
 * No test may leave React mounted.
 *
 * Testing Library auto-registers this cleanup only when vitest runs with
 * `globals: true`, and this project does not — so every `render` / `renderHook`
 * stayed mounted after its test, and 27 files never unmounted at all. React then
 * schedules work that lands after happy-dom has torn the document down, and
 * `window is not defined` arrives with no owner.
 *
 * That is the same failure the fetch guard above exists for, in a second
 * disguise: vitest counts an unowned error and exits 1 with every test passing,
 * intermittently, depending on whether the straggler beats the reporter. On this
 * machine `useChatSession.connection.test.tsx` alone reddened 4 runs in 5 while
 * its 9 tests passed every time.
 *
 * Registered once here rather than per file, because "remember afterEach(cleanup)
 * in every new test file" is the rule that produced the 27.
 */
afterEach(cleanup);

