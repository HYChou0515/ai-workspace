/**
 * web-demo skill — serve a built SPA statically + reverse-proxy /api to the
 * running backend. Light (no compile), so it doesn't OOM like `vite dev`.
 * Node built-ins only. Edit the three constants below, then:
 *   setsid node serve-dist.js > /tmp/serve.log 2>&1 < /dev/null &
 */

const http = require("http");
const fs = require("fs");
const path = require("path");

// --- config ------------------------------------------------------------------
const DIST = "/abs/path/to/web/dist"; // the built SPA (pnpm build output)
const PORT = 8011; // a FREE port to serve on (verify it isn't already taken)
const API = { host: "localhost", port: 8000 }; // where the backend serves /api
// -----------------------------------------------------------------------------

const CT = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".map": "application/json",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".ttf": "font/ttf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
};

http
  .createServer((req, res) => {
    // /api → backend (streams request + response bodies, so SSE works too)
    if (req.url.startsWith("/api")) {
      const p = http.request(
        {
          host: API.host,
          port: API.port,
          path: req.url,
          method: req.method,
          headers: { ...req.headers, host: `${API.host}:${API.port}` },
        },
        (pr) => {
          res.writeHead(pr.statusCode, pr.headers);
          pr.pipe(res);
        },
      );
      p.on("error", (e) => {
        res.writeHead(502);
        res.end("proxy: " + e.message);
      });
      req.pipe(p);
      return;
    }
    // static file, with SPA fallback to index.html for client routes
    let fp = path.join(DIST, decodeURIComponent(req.url.split("?")[0]));
    if (!fp.startsWith(DIST) || !fs.existsSync(fp) || fs.statSync(fp).isDirectory()) {
      fp = path.join(DIST, "index.html");
    }
    fs.readFile(fp, (err, data) => {
      if (err) {
        res.writeHead(404);
        res.end();
        return;
      }
      res.writeHead(200, { "content-type": CT[path.extname(fp)] || "application/octet-stream" });
      res.end(data);
    });
  })
  .listen(PORT, () => console.log(`serving ${DIST} on :${PORT}  (/api → :${API.port})`));
