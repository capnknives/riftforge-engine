"""Load selected keys from the repo-root ``.env`` into ``os.environ``.

Docker Compose injects host ``.env`` only at container create time. Live ops
often tune lag knobs (``RIFTFORGE_SQLITE_JOURNAL``, deploy coalesce, …) and
then want a **game-only restart** (hard rule 19) without recreating the
gateway container. The watcher calls :func:`apply_repo_env` before each game
child spawn so those edits take effect on the next copyover/restart.

The **game child** also calls this from ``server.py`` ``main()`` (and the
Ash viewport starter) because the watcher process can cache an old
``engine.env_file`` for days — new whitelist keys such as
``RIFTFORGE_VIEWPORT`` would otherwise stay unset until a compose recreate
(forbidden by default). ``docker compose exec printenv`` is the container's
create-time env, not the game process.
"""

from __future__ import annotations

import os


def apply_repo_env(root, *, keys=None):
    """Merge whitelisted ``.env`` assignments into ``os.environ`` (no raise)."""
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    if keys is None:
        keys = DEFAULT_REPO_ENV_KEYS
    wanted = set(keys)
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                if name not in wanted:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ[name] = value
    except OSError:
        return


# Keys safe to refresh on game-only restart (lag / persistence / deploy pacing).
DEFAULT_REPO_ENV_KEYS = (
    "RIFTFORGE_SQLITE_JOURNAL",
    "RIFTFORGE_SQLITE_WAL_AUTOCHECKPOINT",
    "RIFTFORGE_SQLITE_WAL_CHECKPOINT",
    "RIFTFORGE_SQLITE_WAL_CHECKPOINT_MIN_BYTES",
    "RIFTFORGE_SQLITE_WAL_BOOT_CHECKPOINT_MIN_BYTES",
    "RIFTFORGE_SQLITE_WAL_AUTOSAVE_TRUNCATE_MIN_BYTES",
    "RIFTFORGE_AUTO_DEPLOY_COALESCE_S",
    "RIFTFORGE_AUTOSAVE_SKIP_AFTER_DEPLOY_S",
    # Auto-deploy throwaway Game() wait (seconds). Live boot is 3–5 min;
    # the old 180s cap abort-held good merges. Default in boot_probe.py
    # is 600. Also whitelist the on/off so ops can skip without compose.
    "AUTO_DEPLOY_BOOT_PROBE",
    "RIFTFORGE_BOOT_PROBE",
    "RIFTFORGE_BOOT_PROBE_TIMEOUT",
    "AUTO_DEPLOY_BOOT_PROBE_TIMEOUT",
    "RIFTFORGE_PERSIST_APPLY_COMMIT_BATCH",
    "RIFTFORGE_INTER_TICK_GAP_MS",
    "RIFTFORGE_AUTOSAVE_SLOW_MS",
    # Lag P30/P31.4: background writer + Cadence planner thread flags must
    # survive copyover / game-only restart without a compose recreate.
    # Watcher calls apply_repo_env before each game-child spawn -- that is
    # how these knobs turn on (hard rule 19: do not compose-recreate).
    "RIFTFORGE_PERSIST_BACKGROUND_WRITER",
    "RIFTFORGE_PERSIST_SAVE_WALL_BUDGET_MS",
    "RIFTFORGE_PERSIST_WRITER_DRAIN_TIMEOUT_S",
    "RIFTFORGE_PERSIST_APPLY_YIELD_EVERY",
    "RIFTFORGE_PERSIST_COLLECT_YIELD_EVERY",
    "RIFTFORGE_PERSIST_FULL_EVERY",
    "RIFTFORGE_PERSIST_DIRTY_CHAR_CAP",
    "RIFTFORGE_PERSIST_DIRTY_ROOM_CAP",
    "RIFTFORGE_FUEL_TICK_WALL_MS",
    "RIFTFORGE_HUMANITY_ROSTER_WALL_MS",
    "ACCORD_DISPATCH_ACTOR_MS_CAP",
    "RIFTFORGE_PERSIST_OFFLINE_DIRTY_SHARDS",
    "RIFTFORGE_CADENCE_PLANNER_THREAD",
    "RIFTFORGE_CADENCE_PLANNER_MAX_ACTORS",
    # Copyover/boot phase timing (docs/plans -- copyover duration diagnosis).
    # Whitelisted so ops can flip it on for a live boot-time investigation
    # via a game-only restart, without a compose recreate (hard rule 19).
    "RIFTFORGE_BOOT_PROFILE",
    # Discord bridge: refresh webhook/channel map on game-only restart so staff
    # briefs (bug/suggest) and radio mirrors pick up .env edits without a
    # compose recreate (hard rule 19).
    "DISCORD_BRIDGE_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "DISCORD_BRIDGE_CHANNELS",
    "DISCORD_BRIDGE_WEBHOOK_HUNT_TIP",
    "DISCORD_BRIDGE_WEBHOOK_ANGEL_RADIO",
    "DISCORD_BRIDGE_WEBHOOK_OOC",
    "DISCORD_BRIDGE_WEBHOOK_WKNZ",
    "DISCORD_BRIDGE_WEBHOOK_BUG_REPORT",
    "DISCORD_BRIDGE_WEBHOOK_SUGGESTION",
    "DISCORD_BRIDGE_WEBHOOK_PATCH_NOTES",
    "DISCORD_BRIDGE_MIN_INTERVAL_SEC",
    "DISCORD_BRIDGE_WKNZ_MUSIC_INTERVAL_SEC",
    # Cursor fixer webhook (GM squashbug / Discord !squashbug).
    "CURSOR_BUG_WEBHOOK_URL",
    "CURSOR_BUG_WEBHOOK_AUTH",
    # Ash viewport (agent play harness). Loopback JSON; game-only restart
    # picks this up without compose recreate (hard rule 19).
    "RIFTFORGE_VIEWPORT",
    "RIFTFORGE_VIEWPORT_BIND",
    "RIFTFORGE_VIEWPORT_ALLOW_NONLOCAL",
    "RIFTFORGE_VIEWPORT_STAFF_ACCOUNT",
    # gm debuglog channel files (engine/gm_debug_export.py). Issue number +
    # env on/off reload on game-only restart -- do not compose-recreate.
    # Token stays the existing RIFTFORGE_DIAG_GITHUB_TOKEN (compose).
    "RIFTFORGE_GM_DEBUG_CAPTURE",
    "RIFTFORGE_GM_DEBUG_GITHUB_ISSUE",
    "RIFTFORGE_GM_DEBUG_LOG_DIR",
)
