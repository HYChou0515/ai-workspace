# Plan — #247 下載檔案/資料夾 (KB + workspace)

Download files and folders from BOTH the KB document IDE and the workspace
(investigation/app-item) file IDE, surfaced through the **shared `FileTree`**.

## Locked decisions (grill-me)

1. **Scope** — one "Download" action in the shared `FileTree` covering all four
   combos: KB file, KB folder, workspace file, workspace folder. File = direct
   download; folder = ZIP.
2. **Single file** — pure FE: `<a download={basename(path)} href={fileDownloadUrl(path)}>`.
   `fileDownloadUrl` resolves per service (KB → `/source-doc/{id}/blobs/{file_id}`,
   workspace → `/a/{slug}/items/{id}/files/{path}`). Always set `download` to the
   basename so KB does not save as the xxh3 hash. No backend change for single file.
3. **Folder / whole ZIP** — reuse the #101 two-step `prepare → stream` pattern
   (downloads_dir temp file + `sweep_stale_downloads` reaper + FE anchor click).
   New endpoints for KB-folder and workspace-folder; share the zip-temp / stream /
   reaper plumbing with the existing collection export.
4. **ZIP contents** — raw original bytes only, **no manifest** (export-to-use, not
   backup; re-importable backup stays the #101 whole-collection export).
5. **ZIP paths / name** — entries relative to the selected folder; zip filename =
   `{folder basename}.zip` (root → collection name / item title).
6. **Tree root** — root also goes through the new raw-file ZIP (KB root = whole
   collection raw files, coexists with #101 manifest export). Entry point = a
   **"Download" button in the Files toolbar header** (new `caps.download`).
7. **Confirm modal** — only the toolbar "Download all" (root) shows a confirm
   modal (with file count). Per-node folder ZIP and single files download directly.
8. **Filtering** — folder/root ZIP skips `.readonly/` (workspace agent diff
   snapshots) and `.gitkeep` (KB folder placeholders).

### Defaults (vetoable)

- KB folder/root ZIP includes every SourceDoc under the prefix regardless of index
  status (original bytes are stored at `store()` time, before indexing).
- Workspace reads go through the existing `WorkspaceFiles` facade (live sandbox if
  warm, snapshot if cold) — download matches what the IDE shows.
- Access control reuses the existing file-read route rules; no new gating.
- No download size cap (reading already-stored files; the 8 MB cap is upload-only).
- New UI strings via `useT` (en source + zh-TW).

### Out of scope for v1

- Multi-select zip (would need a path-list endpoint, not a prefix).
- Manifest-bearing subset export.

## Phases (flat)

- **P1 (BE)** — extract #101 prepare/stream/reaper into a reusable
  `prepare_zip_download(build_fn) → DownloadPrepared` + `stream_zip_download(id)`
  helper; collection export behaviour unchanged (regression test).
- **P2 (BE)** — KB folder/root raw-zip endpoints
  `POST /kb/collections/{id}/folder-download/prepare?prefix=` +
  `GET /kb/collections/{id}/folder-download/{download_id}`. `prefix=""` = whole
  collection; skip `.gitkeep`; entries relative to prefix.
- **P3 (BE)** — workspace folder/root raw-zip endpoints
  `POST /a/{slug}/items/{id}/files/download/prepare?prefix=` +
  `GET /a/{slug}/items/{id}/files/download/{download_id}`. Read via facade; skip
  `.readonly/`; `prefix=""` = whole workspace.
- **P4 (FE)** — `FileService` seam: add `caps.download`, `fileDownloadUrl(path)`,
  `prepareDirDownload(prefix)`, `dirDownloadUrl(downloadId)` to both
  `investigationFileService` and `kbFileService`.
- **P5 (FE)** — `FileTree` wiring: context-menu "Download" (file → anchor, folder →
  prepare+stream), toolbar "Download" button (root + confirm modal showing file
  count), gated by `caps.download`; i18n strings.
- **P6** — full gate: BE `coverage … --fail-under=100` + ruff + ty; FE vitest +
  tsc + build; live smoke (download a file / folder / whole tree on both surfaces).
