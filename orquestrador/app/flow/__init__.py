"""Application layer that runs one inbound purchase message through the flow.

Import submodules explicitly (`app.flow.handler`, `app.flow.extraction`, ...);
this package root stays import-light on purpose so each module can be used
without pulling in the whole flow.
"""

from __future__ import annotations
