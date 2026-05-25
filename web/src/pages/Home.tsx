import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { InvestigationInput } from "../api/types";
import { NewInvestigationModal } from "../components/NewInvestigationModal";
import { TemplatesModal } from "../components/TemplatesModal";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useCreateInvestigation } from "../hooks/useInvestigationMutations";
import { useInvestigations } from "../hooks/useInvestigations";
import { usePersistentDeque, usePersistentSet } from "../hooks/usePersistentSet";
import { useTemplates } from "../hooks/useResources";
import { HomeMain } from "./home/HomeMain";
import { HomeSidebar } from "./home/HomeSidebar";
import {
  EMPTY_FILTERS,
  type Filters,
  type HomeTab,
  type SortDir,
  type SortKey,
} from "./home.helpers";

export function Home() {
  const currentUser = useCurrentUser();
  const result = useInvestigations();
  const createInvestigation = useCreateInvestigation();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<HomeTab>("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [presetTemplate, setPresetTemplate] = useState<string | undefined>(undefined);
  const templates = useTemplates();

  // Seed filters from the URL — breadcrumb topic/product links land here
  // (e.g. /?topic=Reflow%20zone-3&product=MX-7%20board).
  const [filters, setFilters] = useState<Filters>(() => ({
    ...EMPTY_FILTERS,
    topics: searchParams.getAll("topic"),
    products: searchParams.getAll("product"),
  }));
  const [sortKey, setSortKey] = useState<SortKey>("updated");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const pinned = usePersistentSet("rca:pinned");
  const recent = usePersistentDeque("rca:recently_viewed", 12);

  const navigate = useNavigate();

  const handleCreate = async (input: InvestigationInput) => {
    try {
      const created = await createInvestigation.mutateAsync(input);
      setModalOpen(false);
      recent.push(created.resource_id);
      navigate(`/investigations/${created.resource_id}`);
    } catch (err) {
      console.error("createInvestigation failed", err);
      alert(`Create failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const openInvestigation = (id: string) => {
    recent.push(id);
    navigate(`/investigations/${id}`);
  };

  if (result.kind === "loading") {
    return (
      <Shell>
        <Status>Loading investigations…</Status>
      </Shell>
    );
  }
  if (result.kind === "error") {
    return (
      <Shell>
        <Status tone="err">
          Failed to load investigations: {result.error.message}
        </Status>
      </Shell>
    );
  }

  const items = result.items;
  return (
    <div
      data-testid="page-home"
      style={{ display: "flex", minHeight: "100vh", background: "var(--paper)" }}
    >
      <HomeSidebar
        items={items}
        currentUser={currentUser}
        activeTab={tab}
        onTab={setTab}
        pinned={new Set(pinned.values)}
        recent={recent.values}
        filters={filters}
        onFilters={setFilters}
        onNewInvestigation={() => {
          setPresetTemplate(undefined);
          setModalOpen(true);
        }}
        onOpenTemplates={() => setTemplatesOpen(true)}
        onOpenInvestigation={openInvestigation}
        onOpenKnowledge={() => navigate("/kb")}
        onOpenChats={() => navigate("/kb?tab=chats")}
      />
      <HomeMain
        items={items}
        currentUser={currentUser}
        activeTab={tab}
        onTab={setTab}
        filters={filters}
        onFilters={setFilters}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={(k, d) => {
          setSortKey(k);
          setSortDir(d);
        }}
        pinned={new Set(pinned.values)}
        recent={recent.values}
        togglePin={pinned.toggle}
        onOpenInvestigation={openInvestigation}
      />
      <TemplatesModal
        open={templatesOpen}
        templates={templates}
        onClose={() => setTemplatesOpen(false)}
        onPick={(profile) => {
          setTemplatesOpen(false);
          setPresetTemplate(profile);
          setModalOpen(true);
        }}
      />
      <NewInvestigationModal
        open={modalOpen}
        onSubmit={handleCreate}
        onClose={() => setModalOpen(false)}
        initialTemplate={presetTemplate}
      />
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div
      data-testid="page-home"
      style={{ display: "flex", minHeight: "100vh", background: "var(--paper)" }}
    >
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 40,
        }}
      >
        <div className="caps">INVESTIGATIONS</div>
        <span style={{ width: 12 }} />
        {children}
      </div>
    </div>
  );
}

function Status({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "err";
}) {
  return (
    <div
      style={{
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
        fontSize: "var(--text-body)",
      }}
    >
      {children}
    </div>
  );
}
