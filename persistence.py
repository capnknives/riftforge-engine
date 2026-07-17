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
    load_game_time,
    load_moral_state,
    load_rumor_boards,
    load_world,
    mark_seeded,
    save_calendar_epoch_day,
    save_game_time,
    save_moral_state,
    save_rumor_boards,
    save_world,
)
