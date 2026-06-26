"""sandbox-host — a standalone HTTP service that runs isolated process sandboxes.

Completely independent of `workspace_app`: shares no Python modules and no
runtime dependencies. Its only contract with the app is the HTTP wire API
(`docs/sandbox-host-wire.md`) that the app's `HttpSandbox` client speaks.
"""
