// A WUI written with a build step. Copy this when the page is complex enough
// that hand-written DOM stops paying — a form with real validation, a table
// with sorting and filtering, anything with state worth naming.
//
// The bridge needs nothing special here. `window.workspace` is a plain global
// that exists before your first line runs, so it is read in an effect and
// nothing about React changes how it behaves.

import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

// With a build step the bundler owns the stylesheet: import it here and Vite
// emits it into `dist/` and links it from `dist/index.html` itself. Write one —
// a page with none is not "unstyled", it is the browser's 1995 defaults, and
// that is what the person who asked for the page sees first.
import "./styles.css";

const DATA = "data.json"; // in THIS page's folder — the only place it may write

/** Read this page's own data, treating absence as the empty start. */
function useRows() {
  const [rows, setRows] = useState(null); // null = still reading
  const [problem, setProblem] = useState("");

  const reload = useCallback(() => {
    window.workspace
      .readFile(DATA)
      .then((file) => setRows(JSON.parse(file.text)))
      // First run: the file is not there yet. That is the documented way to
      // start empty, and the platform does not report it as a fault.
      .catch(() => setRows([]));
  }, []);

  useEffect(reload, [reload]);

  // Somebody else — a colleague, or the agent — changed the file underneath.
  // Reload only when there is nothing unsaved to lose; see the editor example
  // for the case where there is.
  useEffect(() => {
    window.workspace.onFileChanged((path) => {
      if (path.endsWith("/" + DATA)) reload();
    });
  }, [reload]);

  const save = useCallback((next) => {
    setRows(next);
    window.workspace
      .writeFile(DATA, JSON.stringify(next, null, 2))
      // Show the platform's sentence, not one of your own: it names the thing
      // the reader can change.
      .then(() => setProblem(""))
      .catch((err) => setProblem(err.message));
  }, []);

  return { rows, problem, save };
}

function App() {
  const { rows, problem, save } = useRows();
  const [me, setMe] = useState("");
  const [draft, setDraft] = useState("");

  useEffect(() => {
    window.workspace.whoami().then((who) => setMe(who.user)).catch(() => setMe(""));
  }, []);

  if (rows === null) return <p>Reading…</p>;

  return (
    <div>
      <h1>Built page</h1>
      {problem ? <p role="alert" className="problem">{problem}</p> : null}

      <section data-wui="add">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Note" />
        <button
          disabled={!draft.trim()}
          onClick={() => {
            save([...rows, { text: draft.trim(), by: me }]);
            setDraft("");
          }}
        >
          Add
        </button>
      </section>

      <section data-wui="rows">
        {rows.length === 0 ? (
          <p className="empty">Nothing yet.</p>
        ) : (
          <ul>
            {rows.map((row, i) => (
              <li key={i}>
                {row.text} <small>{row.by}</small>{" "}
                <button onClick={() => save(rows.filter((_, at) => at !== i))}>Remove</button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
