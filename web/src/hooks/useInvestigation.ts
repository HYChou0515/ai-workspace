import { useEffect, useState } from "react";

import { api } from "../api";
import type { FileInfo, Investigation } from "../api/types";

/* ----------------------- single investigation ----------------------- */

type InvState =
  | { kind: "loading" }
  | { kind: "ready"; data: Investigation }
  | { kind: "error"; error: Error };

export function useInvestigation(id: string): InvState {
  const [state, setState] = useState<InvState>({ kind: "loading" });
  useEffect(() => {
    let mounted = true;
    setState({ kind: "loading" });
    api
      .getInvestigation(id)
      .then((data) => mounted && setState({ kind: "ready", data }))
      .catch(
        (e: unknown) =>
          mounted &&
          setState({
            kind: "error",
            error: e instanceof Error ? e : new Error(String(e)),
          }),
      );
    return () => {
      mounted = false;
    };
  }, [id]);
  return state;
}

/* --------------------------- files list ---------------------------- */

type FilesState =
  | { kind: "loading" }
  | { kind: "ready"; items: FileInfo[]; refresh: () => void }
  | { kind: "error"; error: Error; refresh: () => void };

export function useFiles(investigationId: string): FilesState {
  const [items, setItems] = useState<FileInfo[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let mounted = true;
    setError(null);
    api
      .listFiles(investigationId)
      .then((res) => mounted && setItems(res))
      .catch(
        (e: unknown) =>
          mounted &&
          setError(e instanceof Error ? e : new Error(String(e))),
      );
    return () => {
      mounted = false;
    };
  }, [investigationId, tick]);

  const refresh = () => setTick((n) => n + 1);
  if (error) return { kind: "error", error, refresh };
  if (items === null) return { kind: "loading" };
  return { kind: "ready", items, refresh };
}
