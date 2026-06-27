"""Importing this package registers every bundled chart (each chart module
calls ``register(...)`` at import time). The ``chart`` command imports it so the
registry is populated before the schema is built or a request dispatched."""

from sci_plot.charts import (
    box_scatter,  # noqa: F401  (import-for-registration)
    defectmap,  # noqa: F401  (import-for-registration)
    grouped_line,  # noqa: F401  (import-for-registration)
    wafermap,  # noqa: F401  (import-for-registration)
)
