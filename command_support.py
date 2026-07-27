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
    OPPOSITE,
    SELF_NAME_ALIASES,
    _can_see_gm_away,
    _can_see_spirit,
    # Re-export for engine/verbs/basic.py look-at ordinals (#629).
    # Missing this name from the facade makes `look <person>` ImportError
    # at runtime even though the helper lives in engine/command_support.
    _collect_character_matches,
    _display_name,
    _maybe_append_account_tag,
    floor_item_look_lines,
    _find_character,
    _find_item,
    _find_item_prefer_locked,
    _is_gm,
    _is_head_gm,
    _is_staff_gm,
    _is_presence_hidden,
    _move_one,
    _presence_face,
    _presence_hears,
    _public_label,
    _pull_followers,
    is_self_name,
    is_linked_self,
    is_folded,
    is_staff_stealth_presence,
    resolve_named_character,
    resolve_walk_direction,
    start_following,
    start_staff_tail,
    stop_following,
    stop_staff_tail,
    strip_ephemeral_storage_prefix,
)
