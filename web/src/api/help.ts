/**
 * Help API client (#230). Wire shape mirrors `api/help_routes.py`: the platform
 * Help collection id (so the page can scope its KB chat) + the collection's
 * documents (so the page can link each to the KB document viewer). Mock/real
 * swap on the same `VITE_USE_MOCK` switch as the other clients.
 */

import { apiFetch } from "./http";

export type HelpDocKind = "release_notes" | "guide";

export type HelpDocument = {
  /** Opaque SourceDoc id the KB document viewer takes. */
  id: string;
  path: string;
  title: string;
  kind: HelpDocKind;
};

export type HelpInfo = {
  collection_id: string;
  documents: HelpDocument[];
};

/** One `### <group>` block of a release — a Keep a Changelog category (Added /
 * Fixed / Performance / Changed / Documentation) + its bullet items. */
export type ReleaseSection = {
  group: string;
  items: string[];
};

/** One `## [<version>] — <date>` section of the CHANGELOG (#441). */
export type Release = {
  version: string;
  date: string | null;
  sections: ReleaseSection[];
  unreleased: boolean;
};

export type ReleasesInfo = {
  releases: Release[];
};

export type HelpApi = {
  getHelpInfo(): Promise<HelpInfo>;
  getReleases(): Promise<ReleasesInfo>;
};

export const realHelpApi: HelpApi = {
  async getHelpInfo() {
    const r = await apiFetch("/help");
    if (!r.ok) throw new Error(`help info failed: ${r.status}`);
    return (await r.json()) as HelpInfo;
  },
  async getReleases() {
    const r = await apiFetch("/help/releases");
    if (!r.ok) throw new Error(`help releases failed: ${r.status}`);
    return (await r.json()) as ReleasesInfo;
  },
};

export const mockHelpApi: HelpApi = {
  async getHelpInfo() {
    return {
      collection_id: "help-collection",
      documents: [
        { id: "help/getting-started.md", path: "getting-started.md", title: "Getting started", kind: "guide" },
        { id: "help/CHANGELOG.md", path: "CHANGELOG.md", title: "Changelog", kind: "release_notes" },
      ],
    };
  },
  async getReleases() {
    return {
      releases: [
        {
          version: "2026.07.06",
          date: "2026-07-06",
          unreleased: false,
          sections: [
            { group: "Added", items: ["A shiny new thing"] },
            { group: "Fixed", items: ["An annoying bug"] },
            { group: "Documentation", items: ["Tidied the docs"] },
          ],
        },
        {
          version: "2026.07.05",
          date: "2026-07-05",
          unreleased: false,
          sections: [{ group: "Performance", items: ["Faster startup"] }],
        },
      ],
    };
  },
};

const useMock = import.meta.env.VITE_USE_MOCK === "1";

export const helpApi: HelpApi = useMock ? mockHelpApi : realHelpApi;
