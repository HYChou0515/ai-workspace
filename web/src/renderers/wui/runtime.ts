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

  // A blocked subresource announces itself TWICE — the element's error and the
  // policy violation — in an order that varies, so three broken references cost
  // six red lines. One line per URL, whichever arrives first.
  var announced = {};

  function once(url) {
    if (!url) return true;
    if (announced[url]) return false;
    announced[url] = 1;
    return true;
  }

  function report(kind, message, detail) {
    // Capped: a message can contain a URL, and after inlining a URL can BE a
    // multi-megabyte data: payload. It is rendered in the pane and pushed into
    // the chat draft, so an uncapped one is a page freezing the app it reports
    // to. The pick detail is capped separately, on the parent, which is the half
    // a fabricated message can reach.
    var text = String(message);
    post({
      proto: PROTO,
      report: kind,
      message: text.length > 400 ? text.slice(0, 400) + "…" : text,
      detail: detail || null,
    });
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
      //
      // Except when the parent marks it EXPECTED. A page's first run reads a
      // data file that does not exist yet — the documented way to start empty —
      // and reporting that put a red "not allowed" in front of every user
      // opening every new WUI. An alarm that always fires is one nobody reads,
      // which costs exactly the refusals that matter.
      if (!m.expected) report("refused", m.error);
      p.reject(new Error(m.error));
    }
  });

  // CAPTURE phase, deliberately. A script or image that fails to load fires its
  // error ON THE ELEMENT and does not bubble, so a bubble-phase listener sees
  // nothing — and a page whose app.js is misnamed then renders, does nothing,
  // and reports nothing. That silence is the exact failure the assembler leaves
  // broken references in place to avoid.
  window.addEventListener(
    "error",
    function (e) {
      var el = e.target;
      if (el && el.nodeType === 1) {
        var url = el.src || el.href || "";
        if (once(url)) {
          report("error", "This page could not load " + (url || el.tagName.toLowerCase()) + ".");
        }
        return;
      }
      var where = e.filename ? " (" + e.filename + ":" + e.lineno + ")" : "";
      report("error", (e.message || "Something went wrong") + where);
    },
    true,
  );

  // The other half of the same silence: anything the page reaches for that the
  // policy refuses. The browser names it in a console nobody here can open.
  window.addEventListener("securitypolicyviolation", function (e) {
    if (!once(e.blockedURI)) return;
    report(
      "error",
      "This page is not allowed to load " +
        (e.blockedURI || "that") +
        " (" +
        (e.violatedDirective || "blocked") +
        "). A WUI has no network — put the file in its folder, or use a tool.",
    );
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
