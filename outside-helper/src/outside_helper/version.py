"""The helper's version, and the protocol it speaks to the backend.

`__version__` names a release (the pack script stamps it into the wheelhouse
and `outside-helper.json`). `PROTOCOL` changes only when the helper↔backend API
changes incompatibly; the backend refuses a helper below its minimum protocol
(docs/plan-outside-helper.md §3.7.3).
"""

__version__ = "0.1.0"
PROTOCOL = 1
