"""Importing this package registers every bundled chart (each chart module
calls ``register(...)`` at import time). The ``chart`` command imports it so the
registry is populated before the schema is built or a request dispatched."""

from sci_plot.charts import box_scatter  # noqa: F401  (import-for-registration)
from sci_plot.charts import defectmap  # noqa: F401  (import-for-registration)
from sci_plot.charts import grouped_line  # noqa: F401  (import-for-registration)
from sci_plot.charts import wafermap  # noqa: F401  (import-for-registration)
