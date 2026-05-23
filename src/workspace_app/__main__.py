"""Default entry point: `uv run python -m workspace_app`.

Wires the RCA defaults:
  - LocalProcessSandbox (works in any VM/devcontainer without docker)
  - SpecstarFileStore (in-process)
  - LitellmAgentRunner pre-loaded with the RCA system prompt
  - SPA at web/dist if built

Override any piece by importing `workspace_app.api.create_app` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import uvicorn
from specstar import SpecStar

from workspace_app.api import create_app
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.rca.agent import default_rca_agent_config
from workspace_app.sandbox.local_process import LocalProcessSandbox


def main() -> None:
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=LocalProcessSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=LitellmAgentRunner(default_rca_agent_config()),
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
