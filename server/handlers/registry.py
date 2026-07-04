"""cmd -> handler registry.

dispatch() (server.py) looks incoming c2s commands up in HANDLERS; handler modules
register themselves at import time via @register, so wiring a new cmd is one
decorated function in the right domain module. A handler that decides it can't
serve the packet returns context.UNHANDLED and dispatch falls through to the
unhandled log, same as an unregistered cmd.

Handler signature: async fn(session, writer, cmd, params, msg).
"""

HANDLERS = {}


def register(*cmds):
    def deco(fn):
        for c in cmds:
            HANDLERS[c] = fn
        return fn
    return deco
