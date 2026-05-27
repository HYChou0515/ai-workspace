"""csv-column-summary — an example sandbox-provisioned analysis tool.

Reads a CSV and prints a per-column summary (type, counts, nulls, uniques, and
numeric stats / top categorical values). Kept dependency-isolated in its own
repo + venv so the host app never inherits pandas et al.
"""

__version__ = "0.1.0"
