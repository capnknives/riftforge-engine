"""
engine/ -- the generic, game-agnostic MUD engine.

Networking, sessions, hot-reload, and reporting: nothing in this package
knows what a "Tier" or a "Discipline" is. Games register via engine.hooks
(docs/ENGINE_CONSUMER.md). Roadmap to a public Riftforge remote + private
SUPERS: docs/plans/two_repo_purity.md. Shared root files (world.py,
commands.py, persistence.py, maps.py, server.py) are still undecomposed --
see AGENTS.md "Where things live".
"""
