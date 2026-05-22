import { useState } from "react";
import { Chat } from "./Chat";

export function App() {
  const [workspaceId, setWorkspaceId] = useState("default");
  return (
    <div className="app">
      <h1>workspace-app</h1>
      <div className="workspace-bar">
        <label htmlFor="ws">workspace:</label>
        <input
          id="ws"
          value={workspaceId}
          onChange={(e) => setWorkspaceId(e.target.value)}
          placeholder="workspace id"
        />
      </div>
      <Chat workspaceId={workspaceId} />
    </div>
  );
}
