"""discord_staff_auth.py -- allowlist for Discord staff ops commands.

Ops restart/revert/restore/revive from ``tools/discord_staff_ops_bot.py`` must
not rely on Discord role membership alone (sidecar runs lean; role checks need
member cache). Instead, set ``DISCORD_STAFF_USER_IDS`` to a comma-separated
list of Discord user snowflakes allowed to run #ops commands.

Fail closed: unset or empty allowlist → every ops command is refused.
"""

from __future__ import annotations

import os


def parse_staff_user_ids(raw: str | None = None) -> set[str]:
    """Parse ``DISCORD_STAFF_USER_IDS`` into a set of snowflake strings."""
    text = raw if raw is not None else os.environ.get("DISCORD_STAFF_USER_IDS", "")
    text = (text or "").strip()
    if not text:
        return set()
    out: set[str] = set()
    for part in text.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(part)
    return out


def staff_user_allowed(discord_user_id: str, *, raw: str | None = None) -> bool:
    """True when *discord_user_id* is on the configured staff allowlist."""
    allowed = parse_staff_user_ids(raw)
    if not allowed:
        return False
    return str(discord_user_id or "").strip() in allowed
