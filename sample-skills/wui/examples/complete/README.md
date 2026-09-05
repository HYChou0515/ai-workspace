# The complete example — every part of the surface, in one page

The other examples each teach one shape. This one is a page that does the whole
job, so you can see how the pieces sit together before you take any of them
apart.

It is a scrap review board. It reads the item's real records, charts them with a
real library, lets somebody add a note that is saved back, asks a tool for live
status, asks for a review that takes minutes and shows it happening, hands the
user the underlying file, notices a colleague's edit, and shows every failure as
a sentence.

```sh
pnpm install --frozen-lockfile   # or `pnpm install` the first time, to write the lock
pnpm build                       # → tsc --noEmit && vite build → dist/
```

## Read `src/workspace.ts` first

That file is the platform. It is the same for every WUI, and it is where the
mistakes that cannot be SEEN are prevented — the ones that render a page which
is quietly wrong for somebody who cannot open a console.

`src/main.tsx` is just a page. Change it freely.

## The four things worth copying exactly

**`readAll` keeps the files that worked.** `Promise.all` loses 199 good records
to one unreadable file. The failures come back beside the results so the page
can SHOW them — a page that silently drops rows lies about the data.

**`fromItemRoot` for any path somebody else gave you.** A tool with a large
result writes a file and prints the PATH, and it names that file the way the
WORKSPACE does — `scrap-review/out.json`, no leading slash. `readFile` reads a
bare path as one NEXT TO THIS PAGE, so that string becomes
`/scrap-review/scrap-review/out.json`, the folder twice, and the read fails.
This was a real production bug: the page said "nothing found" forever, over a
perfectly good answer.

**`callTool` classifies what came back; it does not assume.** `output` is
whatever bytes the command printed — JSON for one tool, a table for another, a
sentence when there is nothing to report, and for a large result a PATH rather
than the data. Run your tool once and look before you write the parser.

**`reduceRunEvent` ignores what it does not recognise.** It is copied into your
page and will never be edited again; the platform's event set, meanwhile, grows.
A reducer that switches on every known type and throws on the rest breaks the
day a new one appears, in a page whose author has long since moved on.

## The one catch that is ordinary, and the one that is not

```js
.catch(() => setNotes({}))   // FIRST RUN — this page's own file is not there yet
```

That catch is for **your own data file** and nothing else. Absence there is
ordinary and the pane stays quiet about it.

Never wrap a path somebody else gave you in the same catch. A path that cannot
be read is a mistake somebody has to see, and swallowing it is exactly how a
wrong path becomes a permanent, silent "nothing found".

## What to change first

- `RECORDS` — where the item keeps its records
- `Record` — the shape of one of them
- `TOOL` / `JUDGE` — a tool and a workflow **this app actually grants**; see
  "Tools this app offers its WUIs" at the end of `SKILL.md`. The page still
  works with neither: those sections report the refusal instead of breaking.
- the labels — use the words the person who asked for the page used, not the
  field names in the files
