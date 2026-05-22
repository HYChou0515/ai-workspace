import { useCallback, useReducer, useState } from "react";
import { Chat } from "./Chat";
import { FileBrowser } from "./FileBrowser";
import { WorkspaceList } from "./WorkspaceList";

export function App() {
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [filesRefreshTick, bumpFilesRefresh] = useReducer((n: number) => n + 1, 0);

  const onFileMutation = useCallback(() => {
    bumpFilesRefresh();
  }, []);

  return (
    <div className="app">
      <h1>workspace-app</h1>
      <div className="app-grid">
        <WorkspaceList activeId={workspaceId} onSelect={setWorkspaceId} />
        {workspaceId ? (
          <>
            <FileBrowser workspaceId={workspaceId} refreshTick={filesRefreshTick} />
            <Chat workspaceId={workspaceId} onFileMutation={onFileMutation} />
          </>
        ) : (
          <div className="empty-main">Select or create a workspace to begin.</div>
        )}
      </div>
    </div>
  );
}
