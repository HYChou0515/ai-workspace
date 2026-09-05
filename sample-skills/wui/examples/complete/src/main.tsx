// The complete example: every part of the platform's surface, in one page.
//
// It is a scrap review board. It reads the item's real records, charts them with
// a real library, lets someone add a note that is saved back, asks a tool for
// live status, hands the user the underlying file, notices a colleague's edit,
// and shows every failure as a sentence the reader can act on.
//
// Read `src/workspace.ts` first — that is where the platform lives, and where
// the mistakes that cannot be seen from the page are prevented.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Chart from "chart.js/auto";

// The bundler owns the stylesheet: import it and Vite emits it into `dist/` and
// links it from `dist/index.html`. A page with no stylesheet is not "unstyled",
// it is the browser's 1995 defaults.
import "./styles.css";

import {
  callTool,
  fromItemRoot,
  list,
  readAll,
  save,
  sentence,
  textOf,
  whoami,
  reduceRunEvent,
  type Problem,
  type RunProgress,
  type ToolAnswer,
} from "./workspace";

/** Where the item keeps its records. Change this to your item's real shape. */
const RECORDS = "/scrap/";
/** This page's own file. Bare, because it lives next to the page. */
const NOTES = "notes.json";
/** Must appear in `tools:` in page.ai.yaml AND be granted by this app. */
const TOOL = "lot-status";
/** Must appear in `workflows:` in page.ai.yaml AND exist in this app's profile. */
const JUDGE = "judge";

type Record = { lot: string; step: string; qty: number };
type Notes = { [lot: string]: string };

/** One record file, parsed. Files people hand-edit are allowed to be wrong. */
function parseRecord(text: string, path: string): Record | null {
  try {
    const raw = JSON.parse(text) as Partial<Record>;
    if (!raw || typeof raw.lot !== "string") return null;
    return { lot: raw.lot, step: String(raw.step ?? "unknown"), qty: Number(raw.qty ?? 0) };
  } catch {
    // Not a crash: somebody is mid-edit. The caller counts it as a problem and
    // the page says so, rather than dropping the row in silence.
    void path;
    return null;
  }
}

function App() {
  const [records, setRecords] = useState<Record[] | null>(null); // null = still reading
  const [notes, setNotes] = useState<Notes>({});
  const [problems, setProblems] = useState<Problem[]>([]);
  const [me, setMe] = useState("");
  const [answer, setAnswer] = useState<ToolAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const mine = useRef(false); // a write we just made, so its echo is not "somebody else"

  // ── read the item ────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      const files = await list(RECORDS);
      const { rows, problems: bad } = await readAll(
        files.map((f) => f.path),
        parseRecord,
      );
      setRecords(rows);
      setProblems(bad);
    } catch (err) {
      setRecords([]);
      setProblems([{ where: RECORDS, message: sentence(err) }]);
    }
  }, []);

  // ── read this page's own file ────────────────────────────────────────────
  const loadNotes = useCallback(async () => {
    try {
      const file = await window.workspace.readFile(NOTES);
      const text = textOf(file);
      setNotes(text ? (JSON.parse(text) as Notes) : {});
    } catch {
      // FIRST RUN — the file does not exist yet. This catch is for THIS page's
      // OWN file and nothing else: absence here is ordinary and the pane stays
      // quiet about it. Never wrap a path somebody else gave you in a catch like
      // this; that turns a wrong path into a permanent, silent "nothing found".
      setNotes({});
    }
  }, []);

  useEffect(() => {
    void load();
    void loadNotes();
    whoami().then(setMe, () => setMe(""));
  }, [load, loadNotes]);

  // ── somebody else changed something ──────────────────────────────────────
  useEffect(() => {
    window.workspace.onFileChanged((path) => {
      if (mine.current && path.endsWith("/" + NOTES)) {
        mine.current = false; // our own echo
        return;
      }
      if (path.endsWith("/" + NOTES)) void loadNotes();
      else if (path.startsWith(RECORDS)) void load();
    });
  }, [load, loadNotes]);

  // ── write, into this page's folder only ──────────────────────────────────
  const note = async (lot: string, text: string) => {
    const next = { ...notes, [lot]: text };
    setNotes(next); // optimistic: the field must not jump while they type
    mine.current = true;
    try {
      await save(NOTES, next);
    } catch (err) {
      mine.current = false;
      setProblems((p) => [...p, { where: NOTES, message: sentence(err) }]);
    }
  };

  // ── ask a tool, then read what it points at ──────────────────────────────
  const ask = async (lot: string) => {
    setAsking(true);
    setAnswer(null);
    const res = await callTool(TOOL, { lot });
    if (res.kind === "path") {
      // The tool answered with a PATH, not the data. `fromItemRoot` is what
      // stops the folder being applied twice — see the note on it.
      try {
        const file = await window.workspace.readFile(fromItemRoot(res.path));
        const text = textOf(file);
        setAnswer(
          text === null
            ? { kind: "failed", message: `${res.path} is not a text file.` }
            : { kind: "json", value: JSON.parse(text) },
        );
      } catch (err) {
        // NOT swallowed into an empty state. A path we were handed that cannot
        // be read is a mistake somebody has to see.
        setAnswer({ kind: "failed", message: sentence(err) });
      }
    } else {
      setAnswer(res);
    }
    setAsking(false);
  };

  // ── work that takes minutes ──────────────────────────────────────────────
  const [progress, setProgress] = useState<RunProgress | null>(null);

  const judge = async () => {
    // A RUN, not a tool call: this reads the whole folder and asks an agent, so
    // it takes minutes. The page shows progress the whole way — a screen that
    // sits still for two minutes is indistinguishable from a broken one.
    //
    // Closing the page does not cancel it. The run finishes and writes its
    // result back, so "I got bored and closed the tab" costs nothing.
    setProgress({ note: "Starting…", done: false, failed: false });
    try {
      await window.workspace.startRun(JUDGE, { lines: byStep.map(([s]) => s) }, (event) =>
        setProgress((p) => reduceRunEvent(p ?? { note: "", done: false, failed: false }, event)),
      );
    } catch (err) {
      setProgress({ note: sentence(err), done: true, failed: true });
    }
  };

  // ── the chart ────────────────────────────────────────────────────────────
  const byStep = useMemo(() => {
    const totals = new Map<string, number>();
    for (const r of records ?? []) totals.set(r.step, (totals.get(r.step) ?? 0) + r.qty);
    return [...totals.entries()].sort((a, b) => b[1] - a[1]);
  }, [records]);

  const canvas = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    if (!canvas.current || byStep.length === 0) return;
    const chart = new Chart(canvas.current, {
      type: "bar",
      data: {
        labels: byStep.map(([step]) => step),
        datasets: [{ label: "Scrapped", data: byStep.map(([, qty]) => qty) }],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
    // Chart.js keeps a handle on the canvas. Without this, React's next render
    // leaves the old chart attached to a node that is going away — which is how
    // a page starts throwing about a node's missing parent.
    return () => chart.destroy();
  }, [byStep]);

  if (records === null) return <p className="muted">Reading…</p>;

  return (
    <div className="page">
      <header>
        <h1>Scrap review</h1>
        <span className="muted">{me ? `signed in as ${me}` : ""}</span>
      </header>

      {problems.length > 0 && (
        <ul role="alert" className="problems" data-wui="problems">
          {problems.map((p, i) => (
            <li key={i}>
              <strong>{p.where}</strong> — {p.message}
            </li>
          ))}
        </ul>
      )}

      <section data-wui="chart">
        <h2>By step</h2>
        {byStep.length === 0 ? (
          <p className="muted">Nothing in {RECORDS} yet.</p>
        ) : (
          <div className="plot">
            <canvas ref={canvas} />
          </div>
        )}
      </section>

      <section data-wui="judge">
        <h2>Review</h2>
        <button onClick={() => void judge()} disabled={!!progress && !progress.done}>
          Ask for a review
        </button>{" "}
        {progress && (
          <span className={progress.failed ? "problems" : "progress"}>
            {!progress.done && <span className="dot" />}
            {progress.note}
          </span>
        )}
      </section>

      <section data-wui="records">
        <h2>Records</h2>
        {records.length === 0 ? (
          <p className="muted">Nothing to review.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Lot</th>
                <th>Step</th>
                <th>Qty</th>
                <th>Note</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.lot}>
                  <td>{r.lot}</td>
                  <td>{r.step}</td>
                  <td className="num">{r.qty}</td>
                  <td>
                    <input
                      value={notes[r.lot] ?? ""}
                      placeholder="why?"
                      onChange={(e) => void note(r.lot, e.target.value)}
                    />
                  </td>
                  <td>
                    <button onClick={() => void ask(r.lot)} disabled={asking}>
                      {asking ? "Asking…" : "Live status"}
                    </button>{" "}
                    {/* Hands the user the real file, in the workspace beside the
                        page — so they can fix the data where it lives. */}
                    <button onClick={() => void window.workspace.openFile(`${RECORDS}${r.lot}.json`)}>
                      Open file
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {answer && (
        <section data-wui="tool" className={answer.kind === "failed" ? "problems" : ""}>
          <h2>{TOOL}</h2>
          {answer.kind === "failed" ? (
            <p role="alert">{answer.message}</p>
          ) : answer.kind === "json" ? (
            <pre>{JSON.stringify(answer.value, null, 2)}</pre>
          ) : answer.kind === "path" ? (
            <p className="muted">Answered with a path: {answer.path}</p>
          ) : (
            <pre>{answer.text || "(nothing)"}</pre>
          )}
        </section>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
