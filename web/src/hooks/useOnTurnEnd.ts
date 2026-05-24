import { useEffect, useRef } from "react";

/**
 * Fire `cb` exactly when `streaming` transitions true → false — i.e. when an
 * agent turn finishes. Used to re-fetch the file tree so files the agent
 * created/edited via its tools mid-turn become visible (nothing else
 * notifies the listing). Never fires on the initial render or on turn START.
 */
export function useOnTurnEnd(streaming: boolean, cb: () => void): void {
  const wasStreaming = useRef(streaming);
  useEffect(() => {
    if (wasStreaming.current && !streaming) cb();
    wasStreaming.current = streaming;
  }, [streaming, cb]);
}
