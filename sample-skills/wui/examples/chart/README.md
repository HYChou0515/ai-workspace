> ⚠️ **`chart.umd.js` is downloaded by the build. Never edit it** — the next
> Rebuild overwrites it and your change vanishes silently. Change how you call
> the library, or pin a different version in `package.json`'s build step.
>
> ⚠️ **`chart.js` is an example, not a limit.** `npm pack <any-package>@<ver>`
> fetches anything on npm the same way.

# The charting example

**A WUI can use a real charting library. Do not hand-draw one.**

"No network" is about RUNTIME. A `<script src="https://cdn…">` never arrives —
but the library does not have to arrive at runtime. It only has to be a **file
in this folder**, and a folder-relative reference is inlined exactly like
`app.js` and `style.css` are.

Two ways to get it there. This example uses the first.

## 1. A file in the folder (what this example does)

`package.json` has one build step, and it is not a bundler — it fetches the
library:

```sh
npm pack chart.js@4 --silent   # downloads the published tarball
tar xzf chart.js-*.tgz
cp package/dist/chart.umd.js . # the one file the page references
rm -rf package chart.js-*.tgz
```

So **opening the page is enough**: the pane rebuilds a page with a build step
when you open it, the build fetches the library, and the chart draws. You can
also press **Rebuild** above the page, and watch it happen.

The sandbox has a network; the page does not. That is the whole trick.

Any library with a UMD build works this way — measured with Chart.js 4.5.1
(208 KB), which loads, renders, hit-tests and shows tooltips inside the page's
`default-src 'none'` sandbox with no CSP complaint. Prefer the small ones: the
file is inlined into the document, so its size is paid on every open.

## 2. A bundler (see `../react/`)

`pnpm add chart.js`, `import Chart from "chart.js/auto"`, `pnpm build`. Right
when the page is big enough to want a build anyway. It costs a real build — tens
of seconds — where the file above costs one download.

## What this example is careful about

- **A canvas has no intrinsic size.** The stylesheet gives `.plot` a height. A
  chart in a box with no height collapses to nothing, and nothing about that
  failure says so.
- **A missing library is a sentence, not a blank page.** If `chart.umd.js` is
  not there yet the page says so in one line and still shows the table. A page
  that throws on a missing global renders nothing at all, and the reader cannot
  tell which of the two things is wrong.
- **Reading stays the same.** `Chart` is a global that exists before `app.js`
  runs, exactly like `workspace` is. Nothing about the bridge changes.
