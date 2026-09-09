// A WUI that reads the item and lays it out. Copy this when the data already
// exists and somebody wants to SEE it differently.
//
// Three things worth copying, none of which are obvious:
//   1. List once, then read — and read in parallel, or a folder of 200 files
//      takes 200 round trips one after another.
//   2. Parse defensively. These files are written by people and by an agent;
//      a missing field is normal, not an error.
//   3. Hand the user back to the real file. `openFile` is what makes a page
//      part of the workspace instead of a dead end.

var FOLDER = "/issues/"; // where this item keeps its records — change to suit

var all = [];

var $ = function (id) {
  return document.getElementById(id);
};

function status(text, bad) {
  var el = $("status");
  el.textContent = text || "";
  el.className = "status" + (bad ? " status--bad" : "");
}

function failed(err) {
  // The message is written for the person reading the page. Show it; do not
  // replace it with one of your own.
  status(err && err.message ? err.message : String(err), true);
}

/**
 * The `---` block at the top of a markdown file, as a plain object.
 *
 * Deliberately small and forgiving: these files are hand-edited, so a missing
 * block, a missing field or a stray colon must produce a row with blanks rather
 * than an exception that empties the whole page.
 */
function frontmatter(text) {
  var out = {};
  var match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!match) return out;
  match[1].split(/\r?\n/).forEach(function (line) {
    var at = line.indexOf(":");
    if (at < 1) return;
    var key = line.slice(0, at).trim();
    var value = line.slice(at + 1).trim();
    out[key] = value.replace(/^["']|["']$/g, "");
  });
  return out;
}

function numberOf(path) {
  var name = path.slice(path.lastIndexOf("/") + 1);
  return name.replace(/\.md$/i, "");
}

function load() {
  status("Reading…");
  return workspace
    .listFiles(FOLDER)
    .then(function (listing) {
      var paths = listing.files
        .map(function (f) {
          return f.path;
        })
        .filter(function (p) {
          return /\.md$/i.test(p);
        });
      // In PARALLEL. Awaiting each read in a loop turns a folder of 200 files
      // into 200 sequential round trips, and the page just sits there.
      return Promise.all(
        paths.map(function (path) {
          return workspace
            .readFile(path)
            .then(function (file) {
              var meta = frontmatter(file.text);
              return {
                path: path,
                number: numberOf(path),
                title: meta.title || "(untitled)",
                status: meta.status || "",
                owner: meta.owner || "",
              };
            })
            .catch(function () {
              // One unreadable file must not lose the other 199.
              return { path: path, number: numberOf(path), title: "(could not read)", status: "", owner: "" };
            });
        }),
      );
    })
    .then(function (rows) {
      all = rows.sort(function (a, b) {
        return Number(a.number) - Number(b.number) || a.number.localeCompare(b.number);
      });
      status(all.length ? "" : "Nothing in " + FOLDER);
      return all;
    });
}

function statuses() {
  var seen = {};
  all.forEach(function (r) {
    if (r.status) seen[r.status] = 1;
  });
  return Object.keys(seen).sort();
}

function renderFilter() {
  var select = $("status-filter");
  var chosen = select.value;
  select.textContent = "";
  ["(all)"].concat(statuses()).forEach(function (name) {
    var option = document.createElement("option");
    option.textContent = name;
    select.appendChild(option);
  });
  if (chosen) select.value = chosen;
}

function renderTotals(rows) {
  var counts = {};
  rows.forEach(function (r) {
    var key = r.status || "(none)";
    counts[key] = (counts[key] || 0) + 1;
  });
  var box = $("totals");
  box.textContent = "";
  Object.keys(counts)
    .sort()
    .forEach(function (key) {
      var chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = key + ": " + counts[key];
      box.appendChild(chip);
    });
}

function render() {
  var want = $("status-filter").value;
  var rows = all.filter(function (r) {
    return !want || want === "(all)" || r.status === want;
  });

  renderTotals(rows);
  var body = $("rows");
  body.textContent = "";
  rows.forEach(function (row) {
    var tr = document.createElement("tr");
    [row.number, row.title, row.status, row.owner].forEach(function (value) {
      var td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });

    var actions = document.createElement("td");
    var open = document.createElement("button");
    open.textContent = "Open";
    open.onclick = function () {
      // The page is a VIEW of these files, not a replacement for them. When
      // somebody wants the detail, give them the real file in the workspace
      // beside the page rather than reinventing an editor here.
      workspace.openFile(row.path).catch(failed);
    };
    actions.appendChild(open);
    tr.appendChild(actions);
    body.appendChild(tr);
  });

  $("empty").hidden = rows.length > 0;
}

$("status-filter").onchange = render;
$("reload").onclick = function () {
  load().then(render).catch(failed);
};

// Somebody else edited one of the files this page is showing. Nothing is
// half-typed here — it writes nothing — so reloading is always safe, which is
// the whole difference from the editor example.
workspace.onFileChanged(function (path) {
  if (path && path.indexOf(FOLDER) === 0) {
    load().then(render).catch(failed);
  }
});

load()
  .then(function () {
    renderFilter();
    render();
  })
  .catch(failed);
