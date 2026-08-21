"""Load selected keys from the repo-root ``.env`` into ``os.environ``.

Docker Compose injects host ``.env`` only at container create time. Live ops
often tune lag knobs (``RIFTFORGE_SQLITE_JOURNAL``, deploy coalesce, …) and
then want a **game-only restart** (hard rule 19) without recreating the
gateway container. The watcher calls :func:`apply_repo_env` before each game
child spawn so those edits take effect on the next copyover/restart.
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
    "RIFTFORGE_AUTO_DEPLOY_COALESCE_S",
    "RIFTFORGE_AUTOSAVE_SKIP_AFTER_DEPLOY_S",
    "RIFTFORGE_PERSIST_APPLY_COMMIT_BATCH",
    "RIFTFORGE_INTER_TICK_GAP_MS",
    "RIFTFORGE_AUTOSAVE_SLOW_MS",
)
