// A chart over the records the item already holds.
//
// The one thing this example is FOR: the library is a file in the folder, so
// `Chart` is a global that exists before this line runs — exactly like
// `workspace` is. Nothing else about the page changes because of it.
//
// If the file is not there yet, say so in one line and still show the table.
// A page that throws on a missing library renders nothing at all, and the
// reader is left with a blank pane and no idea which of the two is wrong.

var FOLDER = "/issues/"; // where this item keeps its records — change to suit

var $ = function (id) { return document.getElementById(id); };

function status(text, bad) {
  var el = $("status");
  el.textContent = text || "";
  el.className = "status" + (bad ? " status--bad" : "");
}

function failed(err) {
  // The platform's own sentence: it names the thing the reader can act on.
  status(err && err.message ? err.message : String(err), true);
}

/** The `---` block at the top of a markdown file, as a plain object. Forgiving
 *  on purpose: these files are hand-edited, so a missing field is a blank
 *  rather than an exception that empties the page. */
function frontmatter(text) {
  var out = {};
  var match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!match) return out;
  match[1].split(/\r?\n/).forEach(function (line) {
    var at = line.indexOf(":");
    if (at < 1) return;
    out[line.slice(0, at).trim()] = line.slice(at + 1).trim().replace(/^["']|["']$/g, "");
  });
  return out;
}

function load() {
  status("Reading…");
  return workspace.listFiles(FOLDER).then(function (listing) {
    var paths = listing.files
      .map(function (f) { return f.path; })
      .filter(function (p) { return /\.md$/i.test(p); });
    // In PARALLEL. A folder of 200 files read in a loop is 200 round trips one
    // after another, and the page just sits there.
    return Promise.all(
      paths.map(function (path) {
        return workspace
          .readFile(path)
          .then(function (file) {
            var m = frontmatter(file.text);
            var wafers = parseInt(m.wafers, 10);
            return {
              path: path,
              number: path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/i, ""),
              title: m.title || "(no title)",
              cause: m.cause || "unclassified",
              wafers: isNaN(wafers) ? 0 : wafers,
              date: m.date || ""
            };
          })
          // One unreadable file must not lose the other 199.
          .catch(function () {
            return { path: path, number: "?", title: "(could not read)",
                     cause: "unclassified", wafers: 0, date: "" };
          });
      })
    );
  });
}

function byCause(rows) {
  var sums = {};
  rows.forEach(function (r) { sums[r.cause] = (sums[r.cause] || 0) + r.wafers; });
  return Object.keys(sums)
    .map(function (name) { return { name: name, wafers: sums[name] }; })
    .sort(function (a, b) { return b.wafers - a.wafers || a.name.localeCompare(b.name); });
}

var chart = null;

function draw(rows) {
  if (typeof Chart === "undefined") {
    // The build has not run in this workspace yet — see README.md. The table
    // below still works, so the page is useful while that is sorted out.
    $("empty").hidden = false;
    $("empty").textContent =
      "chart.umd.js is not in this folder yet — press Rebuild above, or run the build step in README.md.";
    return;
  }

  var data = byCause(rows);
  if (!data.length) { $("empty").hidden = false; return; }
  $("empty").hidden = true;

  var total = data.reduce(function (n, d) { return n + d.wafers; }, 0) || 1;
  var running = 0;
  var share = data.map(function (d) { running += d.wafers; return Math.round((running / total) * 100); });

  if (chart) chart.destroy();
  chart = new Chart($("pareto"), {
    data: {
      labels: data.map(function (d) { return d.name; }),
      datasets: [
        { type: "bar", data: data.map(function (d) { return d.wafers; }),
          backgroundColor: "#33506e", borderRadius: 2, order: 2, yAxisID: "y" },
        { type: "line", data: share, borderColor: "#c2683a", backgroundColor: "#c2683a",
          borderWidth: 1.5, pointRadius: 2.5, order: 1, yAxisID: "pct" }
      ]
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function (c) {
              return c.dataset.type === "line" ? c.parsed.y + "% running" : c.parsed.y + " wafers";
            }
          }
        }
      },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, title: { display: true, text: "wafers" } },
        pct: { position: "right", beginAtZero: true, max: 100, grid: { display: false },
               ticks: { callback: function (v) { return v + "%"; } } }
      }
    }
  });
}

function render(rows) {
  var dates = rows.map(function (r) { return r.date; }).filter(Boolean).sort();
  $("range").textContent = dates.length
    ? dates[0] + " to " + dates[dates.length - 1] + " · " + FOLDER
    : "No dated records in " + FOLDER;

  draw(rows);

  var body = $("rows");
  body.textContent = "";
  rows.forEach(function (r) {
    var tr = document.createElement("tr");
    [r.number, r.title, r.cause].forEach(function (v) {
      var td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });

    var n = document.createElement("td");
    n.className = "num";
    n.textContent = r.wafers || "—";
    tr.appendChild(n);

    var actions = document.createElement("td");
    var open = document.createElement("button");
    open.type = "button";
    open.textContent = "Open";
    // The page is a VIEW of these records, not a replacement for them.
    open.onclick = function () { workspace.openFile(r.path).catch(failed); };
    actions.appendChild(open);
    tr.appendChild(actions);

    body.appendChild(tr);
  });
  status(rows.length ? "" : "Nothing in " + FOLDER);
}

// Somebody else changed one of these records. This page writes nothing, so
// reloading is always safe — which is the whole difference from the editor.
workspace.onFileChanged(function (path) {
  if (path && path.indexOf(FOLDER) === 0) load().then(render).catch(failed);
});

load().then(render).catch(failed);
