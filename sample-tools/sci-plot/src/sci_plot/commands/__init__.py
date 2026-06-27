"""Exported commands for the 3-stage CLI dispatcher. sci-plot has a single
command, ``chart`` (named so it doesn't collide with csv-column-summary's
``plot`` when both packages are in one agent's allowed_tools); the catalog of
chart *types* lives inside it as the registry-built discriminated union."""

from sci_plot.commands import chart

COMMANDS = {
    "chart": chart,
}
