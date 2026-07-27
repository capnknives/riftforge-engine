"""
engine/ -- the generic, game-agnostic MUD engine.

Networking, sessions, hot-reload, and reporting: nothing in this package
knows what a "Tier" or a "Discipline" is. Games register via engine.hooks
(docs/ENGINE_CONSUMER.md). Public engine remote + private SUPERS remotes
are cut (docs/plans/two_repo_purity.md Phases 0–6). Root `world.py` /
`persistence.py` / `command_support.py` are thin facades over this package;
`server.py`, `commands.py`, and `maps.py` remain shared undecomposed glue
(optional hygiene — see docs/plans/codebase_health_audit_2026-07-20.md and
AGENTS.md "Where things live").
"""
