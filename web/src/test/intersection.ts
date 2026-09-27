/**
 * A controllable `IntersectionObserver` for tests: nothing is "in view" until
 * the test says so with `scrollIntoView()`, which fires every live observer's
 * callback for the elements it watches.
 */
import { vi } from "vitest";

type Watch = { cb: IntersectionObserverCallback; targets: Set<Element>; live: boolean; self: IntersectionObserver };

export function stubIntersectionObserver() {
  const watches: Watch[] = [];
  class FakeIntersectionObserver {
    private w: Watch;
    constructor(cb: IntersectionObserverCallback) {
      this.w = { cb, targets: new Set(), live: true, self: this as unknown as IntersectionObserver };
      watches.push(this.w);
    }
    observe(el: Element) {
      this.w.targets.add(el);
    }
    unobserve(el: Element) {
      this.w.targets.delete(el);
    }
    disconnect() {
      this.w.live = false;
      this.w.targets.clear();
    }
    takeRecords() {
      return [];
    }
  }
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  function fire(isIntersecting: boolean) {
    for (const w of watches) {
      if (!w.live) continue;
      // An element no longer in the document never comes into view, as in a browser.
      const entries = [...w.targets].filter((t) => t.isConnected).map(
        (target) =>
          ({ isIntersecting, intersectionRatio: isIntersecting ? 1 : 0, target }) as unknown as IntersectionObserverEntry,
      );
      if (entries.length) w.cb(entries, w.self);
    }
  }
  return {
    /** Every element any live observer watches enters the viewport. */
    scrollIntoView() {
      fire(true);
    },
    /** Every live observer is told about its elements, but none is in view
     * (the report an observer gets when it starts watching something off screen). */
    reportOutOfView() {
      fire(false);
    },
    /** How many elements live observers are still watching. */
    watching() {
      return watches.filter((w) => w.live).reduce((n, w) => n + w.targets.size, 0);
    },
  };
}
