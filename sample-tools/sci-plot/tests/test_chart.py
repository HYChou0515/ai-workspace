"""The IChart contract surface."""

from __future__ import annotations

import pytest

from sci_plot.framework.chart import IChart


def test_draw_is_abstract():
    """A subclass that defers to ``super().draw`` hits the abstract body."""

    class C(IChart):
        name = "c"

        def draw(self, df, roles, options):
            return super().draw(df, roles, options)

    with pytest.raises(NotImplementedError):
        C().draw(None, {}, None)
