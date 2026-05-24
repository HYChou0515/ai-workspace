/**
 * Brand entry animation shown once on app load: the mark draws in, the apex
 * dot appears, the wordmark rises, then it fades out. Click anywhere to skip;
 * prefers-reduced-motion shortens it to a blink. Mounted at the app root.
 */

import { useEffect, useState } from "react";

import { RcaMark } from "./RcaMark";

type Phase = "in" | "leaving" | "gone";

export function BrandIntro() {
  const [phase, setPhase] = useState<Phase>("in");

  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const hold = reduce ? 150 : 1150; // let the draw-in finish before leaving
    const t1 = setTimeout(() => setPhase("leaving"), hold);
    const t2 = setTimeout(() => setPhase("gone"), hold + 450);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  if (phase === "gone") return null;

  return (
    <div
      className={`brand-intro${phase === "leaving" ? " is-leaving" : ""}`}
      onClick={() => setPhase("gone")}
      aria-hidden
    >
      <div className="brand-intro__lockup">
        <RcaMark size={84} animate />
        <div className="brand-intro__word">
          <span>RCA</span>
          <span className="brand-intro__dot" />
          <span>3.0</span>
        </div>
        <div className="brand-intro__sub">Analysis · AI · Agent</div>
      </div>
    </div>
  );
}
