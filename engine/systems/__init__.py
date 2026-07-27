"""engine/systems/ -- data-driven gameplay frameworks, not infrastructure.

Distinct from the flat engine/ modules (connection, persistence,
tick_registry, content_store, ...): those pair with an existing flat
module or are boot-level plumbing. Everything under systems/ is a small,
generic mechanism a game opts into -- state lives as plain attributes on
Character/Game (each game's own blob codec serializes it), content is
either inline defaults or injected via a set_*() override, and every
module here stays free of `import supers` (docs/plans/two_repo_purity.md).
"""
