"""``python -m workspace_app.graph_preview`` — see what the graph would be (#697).

Reads a collection, runs the computation production runs, writes JSON locally,
and changes nothing. The pure, unit-tested part is ``kb.graph.preview``; this
package is settings-driven composition and CLI glue, like ``workspace_app.worker``.
"""
