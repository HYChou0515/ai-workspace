import { useEffect, useState } from "react";

import { api } from "../api";
import type { FileContent } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "ready"; content: FileContent }
  | { kind: "error"; error: Error };

/**
 * Read a single file. Re-reads when path or `tick` changes; `tick` lets
 * callers force a refresh after they write.
 */
export function useFileContent(
  investigationId: string,
  path: string | null,
  tick = 0,
): State {
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    if (!path) return;
    let mounted = true;
    setState({ kind: "loading" });
    api
      .readFile(investigationId, path)
      .then((content) => mounted && setState({ kind: "ready", content }))
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
  }, [investigationId, path, tick]);
  return state;
}
