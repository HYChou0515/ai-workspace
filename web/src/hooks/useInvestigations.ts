import { useEffect, useState } from "react";

import { api } from "../api";
import type { Investigation } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "ready"; items: Investigation[] }
  | { kind: "error"; error: Error };

export function useInvestigations(): State & { refresh: () => void } {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let mounted = true;
    setState({ kind: "loading" });
    api
      .listInvestigations()
      .then((items) => {
        if (mounted) setState({ kind: "ready", items });
      })
      .catch((e: unknown) => {
        if (!mounted) return;
        const error =
          e instanceof Error ? e : new Error(String(e ?? "unknown error"));
        setState({ kind: "error", error });
      });
    return () => {
      mounted = false;
    };
  }, [tick]);

  return { ...state, refresh: () => setTick((n) => n + 1) };
}
