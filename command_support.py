"""command_support.py -- thin re-export facade (two-repo purity Phase 2b:
docs/plans/two_repo_purity.md).

This module's helpers used to live here directly and reach into `supers`
for a couple of shared move/spirit-sight checks (the one exemption from the
Phase 2 engine-purity gate, since this file sits at the repo root, not
under `engine/` -- see AGENTS.md's "Where things live"). They now live in
`engine/command_support.py`, hookified via `engine.hooks` the same way
`engine/verbs/basic.py`'s old lazy SUPERS imports were in Phase 2, so the
helpers BOTH verb packages need are supers-agnostic at the source too.

This file exists purely so every existing `from command_support import X`
callsite across the codebase (`engine/verbs/basic.py`, `supers/verbs/*`,
`commands.py`) keeps working unchanged.
"""

from engine.command_support import (
    DIRECTIONS,
    _can_see_spirit,
    _display_name,
    _find_character,
    _find_item,
    _find_item_prefer_locked,
    _is_gm,
    _is_head_gm,
    _is_staff_gm,
    _move_one,
    _pull_followers,
    start_following,
    stop_following,
)
