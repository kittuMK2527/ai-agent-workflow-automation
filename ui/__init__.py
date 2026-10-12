"""Presentation layer for the Streamlit app.

* ``theme``      - the design tokens and stylesheet (light and dark)
* ``icons``      - inline SVG icons
* ``components`` - pure functions that return escaped HTML strings (no Streamlit import, so they are unit-testable)
* ``state``      - derives the pipeline stages and other view state from an ``AgentResponse``

Everything dynamic that reaches the page goes through ``html.escape`` in ``components``.
"""
