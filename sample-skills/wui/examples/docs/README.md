# A documentation site, as a WUI

The page is a static-site generator's whole output: `mkdocs build` turns
`docs/*.md` into `site/` — one HTML file per page, a search index, the theme's
JavaScript — and `page.ai.yaml` opens `site/index.html`. Links between pages,
`#anchors` and the search box all work, because
the platform serves the folder from a real address the way any static file
server would.

**Copy this when** somebody wants pages to READ — a handbook, a runbook, notes
that grew past one file — rather than a page that does something.

## Building it

`package.json`'s `build` is what Rebuild and Deploy run:

```
uvx --with mkdocs-material mkdocs build --quiet
```

`uvx`, not `pip install`: the sandbox does not keep a virtualenv or a `--user`
install when it is recycled, and `uvx` grows the generator back on every build,
the way `pnpm install` grows `node_modules`. The build has a network; the page
does not.

Edit the markdown in `docs/`, then Rebuild. Add a page by writing the file and
adding it to `nav:` in `mkdocs.yml`.

## Nothing may need the network

Once the page is running it has none. The theme reaches for it in three places,
and each is turned off here or must be:

| What | Why it reaches out | What to do |
|---|---|---|
| the theme font | Material loads Roboto from Google | `theme: font: false` (already set) |
| `repo_url` | the header asks the code host for stars and the latest release | leave it out |
| a ` ```mermaid ` diagram | Material fetches mermaid from a CDN — and when that fails its **search stops working on every page** | keep the library in the folder: download `mermaid.min.js` into `docs/js/` in the build, and list it under `extra_javascript:`; Material uses the copy it finds |

The page's error panel names each blocked request, so a reach you missed shows
up the first time you open it.
