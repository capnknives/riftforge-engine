"""
persistence.py -- thin re-export facade (two-repo purity Phase 3:
docs/plans/two_repo_purity.md).

The SQLite save/load layer now lives in engine/persistence.py, with zero
SUPERS imports (the two spots that used to reach into supers.balance /
supers.stats directly now go through engine.hooks -- see that module's
docstring). This file exists purely so every existing `persistence.X`
callsite across the codebase (server.py, smoke_test.py) keeps working
unchanged.
"""

from engine.persistence import (
    _MIGRATIONS,
    _schema_version,
    connect,
    is_seeded,
    load_calendar_epoch_day,
    load_cadence_chances,
    load_cadence_scale,
    load_winchester_failsafe,
    load_cadence_airport,
    load_incap_tuning,
    load_outgoing_damage_tuning,
    load_corpse_decay_tuning,
    load_hell_exile_tuning,
    load_pit_drop_tuning,
    load_game_time,
    load_lifetime_stats,
    load_pet_adoption,
    load_moral_state,
    load_plane_soul_counts,
    load_hue_courts,
    load_death_beacons,
    load_author_mantle_event,
    load_ooc_history,
    load_rumor_boards,
    load_world,
    mark_seeded,
    save_calendar_epoch_day,
    save_cadence_chances,
    save_cadence_scale,
    save_winchester_failsafe,
    save_cadence_airport,
    save_incap_tuning,
    save_outgoing_damage_tuning,
    save_corpse_decay_tuning,
    save_hell_exile_tuning,
    save_pit_drop_tuning,
    save_game_time,
    save_lifetime_stats,
    save_pet_adoption,
    save_moral_state,
    save_plane_soul_counts,
    save_hue_courts,
    save_death_beacons,
    save_author_mantle_event,
    save_ooc_history,
    save_rumor_boards,
    save_world,
)
