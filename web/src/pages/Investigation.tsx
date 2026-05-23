import { useParams } from "react-router-dom";

import { useFiles, useInvestigation } from "../hooks/useInvestigation";
import { InvestigationShell } from "./investigation/InvestigationShell";

export function Investigation() {
  const { id } = useParams<{ id: string }>();
  const investigationId = id ?? "";

  const inv = useInvestigation(investigationId);
  const files = useFiles(investigationId);

  if (inv.kind === "loading" || files.kind === "loading") {
    return (
      <ShellMessage>Loading investigation {investigationId}…</ShellMessage>
    );
  }
  if (inv.kind === "error") {
    return <ShellMessage tone="err">{inv.error.message}</ShellMessage>;
  }
  if (files.kind === "error") {
    return <ShellMessage tone="err">{files.error.message}</ShellMessage>;
  }

  return (
    <InvestigationShell
      investigation={inv.data}
      files={files.items}
      dirs={files.dirs}
      onFilesChanged={files.refresh}
    />
  );
}

function ShellMessage({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "err";
}) {
  return (
    <div
      data-testid="page-investigation"
      style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 40,
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
        fontSize: "var(--text-body)",
      }}
    >
      {children}
    </div>
  );
}
