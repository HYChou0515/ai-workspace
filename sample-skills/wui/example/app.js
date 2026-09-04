// A WUI that edits. Copy this file and change what a row is.
//
// The three things that are easy to get wrong, and how they are handled here:
//   1. Saving        — debounced, so one keystroke is not one broadcast.
//   2. Another edit  — noticed, never silently overwritten.
//   3. Failure       — shown in the page, so it is never blank with no reason.

var DATA = "data.json"; // in THIS page's folder — the only place it may write

var rows = [];
var me = "";
var dirty = false; // the user has typed something we have not saved yet

var $ = function (id) {
  return document.getElementById(id);
};

function status(text, bad) {
  var el = $("status");
  el.textContent = text || "";
  el.className = "status" + (bad ? " status--bad" : "");
}

// Every workspace call funnels its failure here. The message is a sentence
// written for the person reading the page — show it, do not replace it.
function failed(err) {
  status(err && err.message ? err.message : String(err), true);
}

function render() {
  var body = $("rows");
  body.textContent = "";
  rows.forEach(function (row, i) {
    var tr = document.createElement("tr");
    [row.lot, row.note, row.by].forEach(function (value) {
      var td = document.createElement("td");
      td.textContent = value || "";
      tr.appendChild(td);
    });
    var actions = document.createElement("td");
    var remove = document.createElement("button");
    remove.textContent = "Remove";
    remove.onclick = function () {
      rows.splice(i, 1);
      render();
      save();
    };
    actions.appendChild(remove);
    tr.appendChild(actions);
    body.appendChild(tr);
  });
  $("empty").style.display = rows.length ? "none" : "block";
}

var timer = null;
function save() {
  dirty = true;
  clearTimeout(timer);
  // Debounced because every write is broadcast to everyone else looking at this
  // item — a save per keystroke is a broadcast per keystroke.
  timer = setTimeout(function () {
    status("Saving…");
    workspace
      .writeFile(DATA, JSON.stringify(rows, null, 2))
      .then(function () {
        dirty = false;
        status("Saved");
      })
      .catch(failed);
  }, 500);
}

function load() {
  return workspace
    .readFile(DATA)
    .then(function (file) {
      return JSON.parse(file.text);
    })
    .catch(function () {
      // Missing file OR unreadable contents: both mean "start empty". This is
      // the first-run path, not an error worth showing.
      return [];
    });
}

$("add").onclick = function () {
  var lot = $("lot").value.trim();
  if (!lot) return;
  rows.push({ lot: lot, note: $("note").value.trim(), by: me });
  $("lot").value = "";
  $("note").value = "";
  render();
  save();
};

// Someone else edited the file we are showing. Last write wins here, so the one
// thing we must not do is quietly reload over what this person is typing.
workspace.onFileChanged(function (path) {
  if (!path || path.indexOf(DATA) === -1) return;
  if (dirty) {
    status("Somebody else changed this too — your unsaved changes are still here.", true);
    return;
  }
  load().then(function (loaded) {
    rows = loaded;
    render();
    status("Updated by somebody else");
  });
});

workspace
  .whoami()
  .then(function (who) {
    me = who.user || "";
  })
  .catch(failed);

load()
  .then(function (loaded) {
    rows = loaded;
    render();
  })
  .catch(failed);
