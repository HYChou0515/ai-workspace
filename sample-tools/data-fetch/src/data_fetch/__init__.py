"""data-fetch — an example sandbox-provisioned tool that materialises a *named*
dataset into the workspace as CSV. It augments a bundled scikit-learn dataset
(bootstrap-resample + jitter + synthetic categorical/datetime/id columns) up to
a large, mixed-dtype table (20k+ rows, 20+ cols), disguised as a domain dataset.

Generates **offline** (no network, no LLM-supplied URL — the agent only picks a
name from the catalog). Kept in its own repo + venv so the host app never
inherits scikit-learn / pandas / numpy.
"""

__version__ = "0.1.0"
