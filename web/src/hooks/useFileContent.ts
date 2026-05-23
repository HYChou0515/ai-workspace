import { useEffect, useRef, useState } from "react";

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

/**
 * Debounced text-file autosave. Returns `{ text, setText, status }` where
 * status indicates the last save outcome — used by the editor breadcrumb
 * "autosaved Xs ago" badge.
 */
export function useAutosave(
  investigationId: string,
  path: string,
  initial: string,
) {
  const [text, setText] = useState(initial);
  const [status, setStatus] = useState<"clean" | "dirty" | "saving" | "saved" | "error">(
    "clean",
  );
  const initialRef = useRef(initial);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    initialRef.current = initial;
    setText(initial);
    setStatus("clean");
  }, [investigationId, path, initial]);

  useEffect(() => {
    if (text === initialRef.current) return;
    setStatus("dirty");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setStatus("saving");
      try {
        await api.writeFile(investigationId, path, text);
        initialRef.current = text;
        setStatus("saved");
      } catch {
        setStatus("error");
      }
    }, 500);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [text, investigationId, path]);

  return { text, setText, status };
}
