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

/**
 * The runtime itself — a real function, not a template.
 *
 * It is serialised with `String(...)` rather than written as a string literal
 * because a string literal is a trap: a backtick anywhere in it (in a COMMENT,
 * typically) closes the template and the module stops parsing. That happened
 * three times. As a function it is also type-checked, linted and formatted like
 * the rest of the file.
 *
 * Two rules it must keep, because serialising a function drops its surroundings:
 * it may reference NOTHING outside its own body — no import, no module
 * constant — and it takes its globals as parameters. The tests run the
 * serialised text through `new Function`, so a violation of the first rule
 * fails there rather than in someone's browser.
 */
function wuiRuntime(window: any, parent: any, document: any): void {
  var PROTO = "wui/1";
  var pending: any = {};
  var seq = 0;
  var fileListeners: any[] = [];
  var picking = false;
  var box: any = null;

  function post(msg: any) {
    // "*" because our own origin is "null" and cannot be named as a target.
    parent.postMessage(msg, "*");
  }

  // A blocked subresource announces itself TWICE — the element's error and the
  // policy violation — in an order that varies, so three broken references cost
  // six red lines. One line per URL, whichever arrives first.
  // No prototype: "constructor" and "toString" are perfectly good keys and an
  // inherited truthy value would silence them.
  var announced: any = Object.create(null);

  function once(url: any) {
    // An empty key says nothing, and a "data" one says almost nothing —
    // Chromium reports every blocked data: URL as that bare string, so keying
    // on it silences EVERY later one and names none of them. Better reported
    // twice than merged into one anonymous line.
    //
    // Length is NOT a reason to skip: a long key is still a unique one. A
    // `url.length > 200` clause here brought back the double report for
    // ordinary long URLs — a signed CDN link is exactly that shape — which is
    // the defect this function exists to prevent, returning under another key.
    if (!url || url === "data") return true;
    if (announced[url]) return false;
    announced[url] = 1;
    return true;
  }

  function report(kind: any, message: any, detail?: any) {
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

  function send(verb: any, args?: any, onEvent?: any) {
    return new Promise(function (resolve: any, reject: any) {
      var id = String(++seq);
      // `onEvent` rides along on the pending entry rather than in a second map:
      // it lives exactly as long as the call, and it goes away with it.
      pending[id] = { resolve: resolve, reject: reject, onEvent: onEvent };
      post({ proto: PROTO, id: id, verb: verb, args: args || {} });
    });
  }

  window.addEventListener("message", function (ev: any) {
    var m = ev.data;
    if (!m || m.proto !== PROTO) return;

    if (m.event === "file_changed") {
      for (var i = 0; i < fileListeners.length; i++) {
        try {
          fileListeners[i](m.path);
        } catch (thrown: any) {
          var why = thrown && thrown.message ? thrown.message : thrown;
          report("error", "A file-changed handler threw: " + why);
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

    // A run's progress: many of these arrive before the one answer does, so it
    // must NOT clear the pending entry. Reported to the page as-is — it decides
    // what to draw, and one it does not recognise is one it can ignore.
    if (m.event === "run_event") {
      if (p.onEvent) {
        try {
          p.onEvent(m.payload);
        } catch (thrown: any) {
          var why2 = thrown && thrown.message ? thrown.message : thrown;
          report("error", "A progress handler threw: " + why2);
        }
      }
      return;
    }

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
    function (e: any) {
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
  window.addEventListener("securitypolicyviolation", function (e: any) {
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

  window.addEventListener("unhandledrejection", function (e: any) {
    var r = e.reason;
    report("error", r && r.message ? r.message : String(r));
  });

  // ── pick mode ────────────────────────────────────────────────────────────
  // The outline is drawn by an element of ours, so it is styled with
  // "all: initial" and an extreme z-index: the page's own CSS is arbitrary
  // agent-written code and would otherwise inherit into, or paint over, the one
  // affordance that has to keep working when the page does not.

  function outline(): any {
    if (box) return box;
    box = document.createElement("div");
    box.setAttribute("data-wui-pick", "");
    box.style.cssText =
      "all: initial; position: fixed; z-index: 2147483647; pointer-events: none;" +
      "border: 2px solid #d23; background: rgba(221,51,51,0.08); display: none;";
    document.body.appendChild(box);
    return box;
  }

  function draw(el: any) {
    var r = el.getBoundingClientRect();
    var b = outline();
    b.style.display = "block";
    b.style.left = r.left + "px";
    b.style.top = r.top + "px";
    b.style.width = r.width + "px";
    b.style.height = r.height + "px";
  }

  function styleSummary(el: any) {
    var cs = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (!cs) return {};
    var keep = [
      "display", "position", "width", "height", "overflow", "color",
      "background-color", "font-size", "flex-direction", "grid-template-columns",
      "text-align", "white-space",
    ];
    var out: any = {};
    for (var i = 0; i < keep.length; i++) out[keep[i]] = cs.getPropertyValue(keep[i]);
    return out;
  }

  function marker(el: any) {
    // An optional aid: a page that labels its parts makes a pick near-certain to
    // land on the right code. Absent, the HTML and styles still identify it.
    for (var n = el; n; n = n.parentElement) {
      if (n.getAttribute && n.getAttribute("data-wui")) return n.getAttribute("data-wui");
    }
    return null;
  }

  // An event's target is not always an element — a click can land on the
  // document itself, and this code must not be the thing that throws.
  function element(t: any) {
    return t && t.nodeType === 1 && t !== box ? t : null;
  }

  function onMove(e: any) {
    var el = element(e.target);
    if (!picking || !el) return;
    draw(el);
  }

  function onPick(e: any) {
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

  function setPicking(on: any) {
    picking = on;
    if (box) box.style.display = "none";
    if (on) document.body.style.cursor = "crosshair";
    else document.body.style.cursor = "";
  }

  document.addEventListener("mousemove", onMove, true);
  document.addEventListener("click", onPick, true);

  window.workspace = {
    listFiles: function (prefix?: any) { return send("listFiles", { prefix: prefix || "" }); },
    readFile: function (path: any) { return send("readFile", { path: path }); },
    startRun: function (workflow: any, payload: any, onEvent: any) {
      return send("startRun", { workflow: workflow, with: payload || {} }, onEvent);
    },
    writeFile: function (path: any, text: any) { return send("writeFile", { path: path, text: text }); },
    deleteFile: function (path: any) { return send("deleteFile", { path: path }); },
    openFile: function (path: any) { return send("openFile", { path: path }); },
    whoami: function () { return send("whoami"); },
    // The page's only way to reach anything outside itself — it has no network
    // of its own, and a credential never comes in here to be spent.
    callTool: function (name: any, args?: any) { return send("callTool", { name: name, args: args || {} }); },
    onFileChanged: function (fn: any) { fileListeners.push(fn); },
  };
}

/** The runtime, as the source of one function `(window, parent, document)`. */
export const WUI_RUNTIME_SOURCE: string = String(wuiRuntime);
/** The runtime as a `<script>` body, ready to inject into the assembled page. */
export function wuiRuntimeScript(): string {
  return `(${WUI_RUNTIME_SOURCE})(window, parent, document);`;
}
