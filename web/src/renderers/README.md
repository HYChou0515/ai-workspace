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
