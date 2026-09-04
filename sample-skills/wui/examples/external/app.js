// A WUI that calls a tool. Copy this when the answer lives in another system.
//
// The page has NO network. `callTool` asks the platform to run a tool, and the
// tool reads the item's credentials — so the secret never enters the browser,
// and this page cannot choose which secret to ask a person for.
//
// Unlike the other two examples this one cannot be copied and just work: the
// tool must be one THIS app grants. Which is why most of the code below is
// about the three ways that fails, each of which a reader has to be able to act
// on. Getting those right is the whole job here.

var TOOL = "lot-status"; // must appear in `tools:` in page.ai.yaml AND be granted

var last = null; // the last good answer, so Save has something to write

var $ = function (id) {
  return document.getElementById(id);
};

function status(text) {
  $("status").textContent = text || "";
}

/**
 * Show why it did not work, in the words the platform used.
 *
 * Do NOT replace these with your own message. Each one names the thing the
 * reader can change, and they are different things:
 *
 *   "did not declare lot-status"  → add it to `tools:` in the view file
 *   "does not offer lot-status"   → this app does not grant it; ask an operator
 *   "is unavailable: ..."         → the tool exists but could not be resolved
 *
 * A page that showed "Lookup failed" for all three would send the reader to the
 * wrong place two times out of three.
 */
function problem(message) {
  var el = $("problem");
  el.textContent = message;
  el.hidden = false;
}

function clear() {
  $("problem").hidden = true;
  $("fields").textContent = "";
  $("raw").hidden = true;
  $("save").disabled = true;
}

function show(data) {
  var list = $("fields");
  list.textContent = "";
  Object.keys(data).forEach(function (key) {
    var dt = document.createElement("dt");
    dt.textContent = key;
    var dd = document.createElement("dd");
    dd.textContent = typeof data[key] === "object" ? JSON.stringify(data[key]) : String(data[key]);
    list.appendChild(dt);
    list.appendChild(dd);
  });
}

function lookUp() {
  var lot = $("lot").value.trim();
  if (!lot) return;
  clear();
  status("Asking " + TOOL + "…");

  workspace
    .callTool(TOOL, { lot: lot })
    .then(function (res) {
      status("");
      // A non-zero exit is the TOOL saying no, not the platform failing. Its
      // own output is the explanation, and it is the only one there is.
      if (res.exit_code !== 0) {
        problem(TOOL + " could not answer for " + lot + ".");
        $("rawtext").textContent = res.output;
        $("raw").hidden = false;
        return;
      }
      // `output` is the tool's stdout, verbatim — nothing is appended to it, so
      // it is safe to parse. It is also not guaranteed to be JSON: that is the
      // tool's contract, not the platform's.
      var data;
      try {
        data = JSON.parse(res.output);
      } catch (e) {
        problem(TOOL + " answered with something this page cannot read.");
        $("rawtext").textContent = res.output;
        $("raw").hidden = false;
        return;
      }
      last = { lot: lot, at: new Date().toISOString(), data: data };
      show(data);
      $("save").disabled = false;
    })
    .catch(function (err) {
      // The refusal from the bridge or the route, verbatim.
      status("");
      problem(err && err.message ? err.message : String(err));
    });
}

// Reaching out and keeping the answer are separate. A page that wrote every
// lookup into the workspace would fill the item with noise nobody asked for —
// and the write is what other people and the agent then see.
function save() {
  if (!last) return;
  status("Saving…");
  workspace
    .writeFile("last-lookup.json", JSON.stringify(last, null, 2))
    .then(function () {
      status("Saved");
    })
    .catch(function (err) {
      status("");
      problem(err && err.message ? err.message : String(err));
    });
}

$("go").onclick = lookUp;
$("save").onclick = save;
$("lot").onkeydown = function (e) {
  if (e.key === "Enter") lookUp();
};
