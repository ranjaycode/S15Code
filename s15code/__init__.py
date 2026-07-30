"""S15Code — the budget-aware agent runtime.

One importable package. The live graph, scoped memory and A2A boundary come from
Session 13; the generative-UI layer from Session 14; ``economics`` and
``telemetry`` are this session's work. There is no second package, and nothing is
nested inside a previous session's namespace.

    s15code.core.live_graph   executor, durable event journal, patches
    s15code.core.memory       typed scoped memory, semantic chunking
    s15code.core.a2a          the agent-to-agent boundary
    s15code.ui                catalog, validator, surface, AG-UI stream, HITL
    s15code.economics         budget, tiers, policy, the hard controller
    s15code.telemetry         the same journal, exported as OTel spans
"""

__version__ = "0.1.0"
