"""``IChart`` — the abstract contract every chart implements (thin renderer).

A chart declares:

* ``name`` / ``description`` — LLM-facing identity (the discriminated-union tag).
* ``roles`` — the columns it needs (see :mod:`sci_plot.framework.roles`).
* ``Options`` — a nested pydantic model of chart-specific knobs.
* ``draw(df, roles, options)`` — plot onto a Figure and return it.

The framework handles read → coerce → resolve-roles → style → save around
``draw``; ``draw`` keeps full post-processing freedom (build a die grid, compute
cumulative %, collapse hierarchical labels, suppress points) and may reconfigure
the figure frame (equal aspect, hidden axes, custom colorbar).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
from matplotlib.figure import Figure
from pydantic import BaseModel

from sci_plot.framework.roles import Role


class _NoOptions(BaseModel):
    """Default empty options for charts with no extra knobs."""


class IChart(ABC):
    """Abstract chart renderer. Subclasses set the class attributes and
    implement :meth:`draw`. Instances are stateless — one is registered per
    chart and reused."""

    #: discriminated-union tag, e.g. ``"box_scatter"``.
    name: str = ""
    #: one-line, LLM-facing description of the chart + what data it needs.
    description: str = ""
    #: the columns this chart binds (resolved by the framework before ``draw``).
    roles: tuple[Role, ...] = ()
    #: chart-specific knobs; a pydantic ``BaseModel`` subclass.
    Options: type[BaseModel] = _NoOptions

    @abstractmethod
    def draw(
        self,
        df: pd.DataFrame,
        roles: dict[str, Any],
        options: BaseModel,
    ) -> Figure:
        """Render ``df`` into a matplotlib ``Figure`` and return it.

        ``roles`` maps each declared role name to the resolved column name
        (``str``) or, for ``multi`` roles, the resolved list of column names
        (``list[str]``). ``options`` is a validated instance of ``Options``.
        """
        raise NotImplementedError
