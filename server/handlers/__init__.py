"""c2s command handlers, one module per domain.

Importing this package registers every handler (each module self-registers its
cmds via @register at import time); server.dispatch() then routes lookups through
HANDLERS. To wire a new cmd: add a decorated async function to the right domain
module here — dispatch() and the transport never change.
"""
from .registry import HANDLERS, register            # noqa: F401
from . import context                               # noqa: F401
from . import (auth, combat_cmds, dev, economy, editors, houses,  # noqa: F401
               items, loot_cmds, patterns_cmds, quests, social, world_cmds)
