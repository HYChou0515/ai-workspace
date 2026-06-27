"""sci-plot — extensible scientific-plotting catalog.

One ``chart`` command (the 3-stage CLI dispatcher), a thick framework that
turns messy tabular input into a clean DataFrame + resolved column roles, and a
registry of ``IChart`` renderers. Adding a chart = one ``IChart`` subclass +
registering it; the command's JSON schema (a discriminated union on ``chart``)
is auto-assembled from the registry.
"""
