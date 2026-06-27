"""The thick framework: input normalization, role resolution, house style +
figure save, and the chart registry / schema assembly.

The framework is uniform in *mechanism* but per-chart in *specification* — every
chart goes through the same read → coerce → resolve-roles → style → draw → save
pipeline, but each chart declares its own ``roles`` + ``Options``, so charts do
NOT all take the same input.
"""
