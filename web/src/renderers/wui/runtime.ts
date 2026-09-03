/**
 * The code we put INSIDE every WUI.
 *
 * It is a string because it runs in the frame, not here — but it is the source
 * of one function, not a blob of statements, so a test can `new Function` it and
 * drive it with a fake window instead of trusting a template by eye. That
 * matters more than usual: this is the half of a WUI that has to work when the
 * agent's half does not.
 *
 * Three jobs, and they are the same job:
 *
 * - `window.workspace` — the seven verbs as promises, so a page never writes
 *   `postMessage` plumbing (and never invents its own protocol).
 * - **Error capture.** A blank page is exactly where someone who cannot open a
 *   console gets stuck. Ours catches, because the agent's code is the code that
 *   just failed.
 * - **Pick mode.** The parent cannot reach into a null-origin frame, so
 *   "point at the bit that looks wrong" can only be done from in here.
 *
 * Written in ES5-ish, dependency-free style: it is injected verbatim into a
 * page we did not write, so it must not assume a build step or a global.
 */

/** The runtime, as the source of one function `(window, parent, document)`. */
export const WUI_RUNTIME_SOURCE = String.raw`function (window, parent, document) {
  var PROTO = "wui/1";
  var pending = {};
  var seq = 0;
  var fileListeners = [];
  var picking = false;
  var box = null;

  function post(msg) {
    // "*" because our own origin is "null" and cannot be named as a target.
    parent.postMessage(msg, "*");
  }

  function report(kind, message, detail) {
    post({ proto: PROTO, report: kind, message: String(message), detail: detail || null });
  }

  function send(verb, args) {
    return new Promise(function (resolve, reject) {
      var id = String(++seq);
      pending[id] = { resolve: resolve, reject: reject };
      post({ proto: PROTO, id: id, verb: verb, args: args || {} });
    });
  }

  window.addEventListener("message", function (ev) {
    var m = ev.data;
    if (!m || m.proto !== PROTO) return;

    if (m.event === "file_changed") {
      for (var i = 0; i < fileListeners.length; i++) {
        try {
          fileListeners[i](m.path);
        } catch (e) {
          report("error", "A file-changed handler threw: " + (e && e.message ? e.message : e));
        }
      }
      return;
    }

    if (m.command === "pick") {
      setPicking(!!m.on);
      return;
    }

    var p = pending[m.id];
    if (!p) return;
    delete pending[m.id];
    if (m.ok) {
      p.resolve(m.value);
    } else {
      // Reported AND rejected: the page may well handle this, but the person
      // looking at it should still be told, and it is what they forward on.
      report("refused", m.error);
      p.reject(new Error(m.error));
    }
  });

  window.addEventListener("error", function (e) {
    var where = e.filename ? " (" + e.filename + ":" + e.lineno + ")" : "";
    report("error", (e.message || "Something went wrong") + where);
  });

  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    report("error", r && r.message ? r.message : String(r));
  });

  // ── pick mode ────────────────────────────────────────────────────────────
  // The outline is drawn by an element of ours, so it is styled with
  // "all: initial" and an extreme z-index: the page's own CSS is arbitrary
  // agent-written code and would otherwise inherit into, or paint over, the one
  // affordance that has to keep working when the page does not.

  function outline() {
    if (box) return box;
    box = document.createElement("div");
    box.setAttribute("data-wui-pick", "");
    box.style.cssText =
      "all: initial; position: fixed; z-index: 2147483647; pointer-events: none;" +
      "border: 2px solid #d23; background: rgba(221,51,51,0.08); display: none;";
    document.body.appendChild(box);
    return box;
  }

  function draw(el) {
    var r = el.getBoundingClientRect();
    var b = outline();
    b.style.display = "block";
    b.style.left = r.left + "px";
    b.style.top = r.top + "px";
    b.style.width = r.width + "px";
    b.style.height = r.height + "px";
  }

  function styleSummary(el) {
    var cs = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (!cs) return {};
    var keep = [
      "display", "position", "width", "height", "overflow", "color",
      "background-color", "font-size", "flex-direction", "grid-template-columns",
      "text-align", "white-space",
    ];
    var out = {};
    for (var i = 0; i < keep.length; i++) out[keep[i]] = cs.getPropertyValue(keep[i]);
    return out;
  }

  function marker(el) {
    // An optional aid: a page that labels its parts makes a pick near-certain to
    // land on the right code. Absent, the HTML and styles still identify it.
    for (var n = el; n; n = n.parentElement) {
      if (n.getAttribute && n.getAttribute("data-wui")) return n.getAttribute("data-wui");
    }
    return null;
  }

  // An event's target is not always an element — a click can land on the
  // document itself, and this code must not be the thing that throws.
  function element(t) {
    return t && t.nodeType === 1 && t !== box ? t : null;
  }

  function onMove(e) {
    var el = element(e.target);
    if (!picking || !el) return;
    draw(el);
  }

  function onPick(e) {
    var el = element(e.target);
    if (!picking || !el) return;
    e.preventDefault();
    e.stopPropagation();
    var r = el.getBoundingClientRect();
    report("pick", "The user pointed at this part of the page.", {
      html: (el.outerHTML || "").slice(0, 4000),
      marker: marker(el),
      rect: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
      styles: styleSummary(el),
    });
    setPicking(false);
  }

  function setPicking(on) {
    picking = on;
    if (box) box.style.display = "none";
    if (on) document.body.style.cursor = "crosshair";
    else document.body.style.cursor = "";
  }

  document.addEventListener("mousemove", onMove, true);
  document.addEventListener("click", onPick, true);

  window.workspace = {
    listFiles: function (prefix) { return send("listFiles", { prefix: prefix || "" }); },
    readFile: function (path) { return send("readFile", { path: path }); },
    writeFile: function (path, text) { return send("writeFile", { path: path, text: text }); },
    deleteFile: function (path) { return send("deleteFile", { path: path }); },
    openFile: function (path) { return send("openFile", { path: path }); },
    whoami: function () { return send("whoami"); },
    // The page's only way to reach anything outside itself — it has no network
    // of its own, and a credential never comes in here to be spent.
    callTool: function (name, args) { return send("callTool", { name: name, args: args || {} }); },
    onFileChanged: function (fn) { fileListeners.push(fn); },
  };
}`;

/** The runtime as a `<script>` body, ready to inject into the assembled page. */
export function wuiRuntimeScript(): string {
  return `(${WUI_RUNTIME_SOURCE})(window, parent, document);`;
}
