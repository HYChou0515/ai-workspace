# File preview renderers

A file is previewed by the renderer matched in **`registry.ts`** — the one place
that maps a path to a renderer. `FileView.tsx` just mounts
`rendererComponent(path)`; pane padding, the preview⇄edit toggle, and the
Outline panel all derive from the same table. **Adding a preview type is one
entry — no other file changes.**

Renderers are backend-agnostic: file IO, the `fileUrl` for embedded refs, and
the listing all come from the **`FileService`** in context (`useFileService()` /
`useFileBuffer` / `useFileList`), never from a hard-wired investigation id. The
same renderers serve the investigation workspace and a KB collection — whichever
service the surrounding `<FileServiceProvider>` injects.

## Built-in types

| key | extensions / match | renderer | notes |
|---|---|---|---|
| `report` | `/report.v{N}.md` | `ReportRenderer` | outline |
| `markdown` | `md`, `markdown` | `MarkdownRenderer` | editToggle, outline |
| `notebook` | `ipynb` | `NotebookRenderer` | cells run in the UI |
| `csv` | `csv`, `tsv` | `CsvRenderer` | editToggle (table preview) |
| `html` | `html`, `htm` | `HtmlRenderer` | editToggle (sandboxed iframe) |
| `image` | `png` `jpg` `jpeg` `gif` `svg` `webp` `bmp` | `ImageRenderer` | editToggle |
| `json` | `json` | `TextRenderer` | rawEditor |
| `text` | _everything else_ | `TextRenderer` | rawEditor (catch-all, keep last) |

## Add a type (incl. a company-internal one)

**1. Write the renderer component** in this folder. It takes `RendererProps`
(`{ path }`), reads the file via `useFileBuffer(path)`, and — if it's a preview
with an edit mode — falls back to the byte editor while editing (so every file
stays editable). Need a URL for an embedded ref? `useFileService().fileUrl(src)`.
Mirror `HtmlRenderer.tsx` / `CsvRenderer.tsx`:

```tsx
import { useEditMode } from "../hooks/editMode";
import { useFileBuffer } from "../hooks/fileBuffer";
import { TextRenderer } from "./TextRenderer";

export function AcmeRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const { entry } = useFileBuffer(path);
  if (isEditing(path)) return <TextRenderer path={path} />;
  if (entry.status === "loading") return <div>Loading {path}…</div>;
  if (entry.status === "error") return <div>{entry.error ?? "load failed"}</div>;
  return <pre>{/* render entry.text however the .acme format wants */}</pre>;
}
```

**2. Add one entry** to `RENDERERS` in `registry.ts` (order matters — first
match wins; keep the `text` catch-all last):

```ts
{ key: "acme", match: ext("acme", "acmez"), Component: AcmeRenderer, editToggle: true },
```

Flags (all optional):

- **`editToggle`** — the type has a preview ⇄ edit (byte editor) duality. The
  tab strip shows an Edit toggle; while editing, the pane goes full-bleed and
  the component should render `TextRenderer` (see above).
- **`rawEditor`** — it _is_ a full-bleed code editor (never a preview), e.g.
  `text`/`json`. Don't combine with `editToggle`.
- **`outline`** — it renders a markdown body; its headings feed the Outline
  panel.

That's it: `pickRenderer`, `FileView`, padding, the edit toggle, and the outline
all pick it up. Add a case to `registry.test.ts` to lock the routing.

(Image MIME types live in `../pages/investigation/renderer.ts::imageMime` — add
a `case` there too if your type is a new image format.)

Note: `RENDERERS` is a const array and **is not open to second-party
registration**. Order is semantics here (first match wins, catch-all last), and
two authors can claim the same extension, so opening it needs an ordering rule
that view kinds don't need. `KbDocBody.tsx` and `WorkspaceShell.tsx` also branch
on the renderer key, so "one entry, no other file changes" isn't quite true yet
either. #698 deliberately opened only the view-kind layer below.

## Add a view kind (`*.ai.yaml`)

A different extension point: `entity/viewKindRegistry.tsx` maps a `view:` name to
a component. `registerViewKind({ kind, Component })` is a plain call and the
built-ins go through it too, so a second-party kind takes the same path the app
exercises on every startup.

A view kind does **not** have to be entity-bound. Declare `needsEntity: true`
only if you draw entity records — that flag decides whether a view file *must*
name an `entity:`, nothing more. What actually drives the entity fetch and the
entity-shaped chrome is `spec.entity`, so a kind without `needsEntity` whose
file names one still gets records. A kind reading workspace files names no
entity, and those props arrive empty.

`parseViewSpec` answers only "is this a view file?" (does it name a kind). It
does not know which kinds exist and does not enforce `entity:` — both moved to
the registry, so adding a kind touches neither the parser nor a TS union. It
does coerce the platform's own fields — the document is arbitrary user YAML, and
widening what parses without widening what is validated let a `title:` mapping
reach a React child and blank the page.

That coercion is an explicit list (`parseViewSpec`), not a schema, so **adding a
field to `ViewSpec` means adding it there too**. A field left off rides raw into
whatever renders it; `ViewErrorBoundary` contains the resulting throw, but the
user still loses the panel. Where a field then indexes a lookup table, type
coercion isn't enough — `week.start` is checked against its enum, because
`start: mon` passed a string check and produced `Invalid time value` from deep
inside the axis code.

Fields still carried raw: `color_by`, `always_week`, `weekday`, `day_of_month`.

The registry also refuses names the container answers to itself (`health`), so a
registration can't succeed and then never render. `ViewErrorBoundary` wraps the
whole panel — the one error boundary in this app, because this is the one place
that runs code the platform team didn't write. It covers the header too, since
that renders `spec.title`, which is where a hostile view file actually crashed.

Maintainer-authored kinds live in `../../ext/`, import solely from
`entity/public.ts` (enforced by `../../ext/imports.test.ts`), and are registered
from `../../ext/index.ts`, which `main.tsx` imports for its side effects.
Guide: `docs/view-kind-authoring.md`. Example: `../../ext/CsvTableView.tsx`.

Test a new kind through the **file**, like `entity/viewKindPlugin.test.tsx` does
— not by calling `resolveViewRenderer` directly. That shortcut is why the
unsupported-kind fallback sat unreachable behind a green test for so long.
