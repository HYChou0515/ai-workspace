"""data-fetch — an example sandbox-provisioned tool that downloads a URL into
the workspace (streaming, so large files don't blow memory). Kept in its own
repo + venv so the host app never inherits an HTTP client it doesn't use.
"""

__version__ = "0.1.0"
