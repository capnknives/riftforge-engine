"""Public JSON sidecar for the brochure (who count + last player-facing ship).

The splash at play.riftforge.me fetches ``GET /status.json``. Nginx aliases
the gitignored repo-root file the same way as ``.discord_invite.json``.

Why this is not a live occupancy widget by default: listing shoppers treat
``0 players`` as a dead game. The site only paints a who-line when the count
is greater than zero. Alpha copy on the HTML still says the world stays up.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from engine import changelog_index
from engine import mssp
from engine.changelog_audience import display_summary, visible_to_viewer


# Default sidecar name; ``engine.style.LOGIN_PUBLIC_STATUS_JSON`` may override.
PUBLIC_STATUS_JSON = ".public_status.json"

# Game package the brochure is for (live SUPERS). Staff-only / other-game
# changelog bullets stay off the public site.
_BROCHURE_GAME = "supers"


def public_status_json_path(root=None):
    """Repo-root sidecar nginx / the gateway serve as ``GET /status.json``."""
    from engine import style as style_mod

    base = root if root is not None else os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    name = getattr(style_mod, "LOGIN_PUBLIC_STATUS_JSON", PUBLIC_STATUS_JSON)
    return os.path.join(base, name)


def _repo_root():
    """Directory that holds ``content/changelog_index.json``."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def latest_player_change(root=None):
    """Newest player-visible SUPERS ``changes`` bullet, or None.

    Uses the compiled changelog index (same feed as in-game ``changes``).
    Missing or unreadable index is fail-soft -- the splash keeps static copy.
    """
    repo = root if root is not None else _repo_root()
    entries = changelog_index.load_index_file(changelog_index.index_path(repo))
    if not entries:
        return None
    ranked = sorted(
        entries,
        key=lambda row: str((row or {}).get("sort_ts") or ""),
        reverse=True,
    )
    for entry in ranked:
        if not isinstance(entry, dict):
            continue
        if not visible_to_viewer(entry, _BROCHURE_GAME, False):
            continue
        summary = display_summary(entry.get("summary") or "")
        if not summary:
            continue
        date = str(entry.get("date") or "").strip()
        return {"date": date, "summary": summary}
    return None


def build_payload(game, *, root=None):
    """JSON body for the public status file."""
    players = int(mssp.player_count(game))
    payload = {
        "players": players,
        "alpha": True,
        "world_persists": True,
        "website": mssp.website_url(game),
    }
    news = latest_player_change(root=root)
    if news:
        payload["last_change"] = news
    return payload


def _fingerprint(payload):
    """Stable compare key -- omit wall-clock so quiet ticks do not rewrite."""
    slim = dict(payload)
    slim.pop("updated_at", None)
    return json.dumps(slim, sort_keys=True, separators=(",", ":"))


def _atomic_write(path, text):
    """Write *text* via a temp file then replace. Return path or None."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"[public_status] write skipped: {exc}", flush=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return path


def publish(game, *, root=None):
    """Atomically write the public status sidecar. Fail-soft on disk errors."""
    path = public_status_json_path(root)
    payload = build_payload(game, root=root)
    payload["updated_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    text = json.dumps(payload, indent=2) + "\n"
    written = _atomic_write(path, text)
    if written and game is not None:
        game._public_status_fingerprint = _fingerprint(payload)
    return written


def maybe_publish(game, *, root=None):
    """Rewrite the sidecar only when who-count or last-change actually moved.

    Called from the heartbeat. Cheap compare; skip disk when nothing changed
    so a 3-second tick does not hammer the bind-mount.
    """
    if game is None:
        return None
    payload = build_payload(game, root=root)
    mark = _fingerprint(payload)
    if mark == getattr(game, "_public_status_fingerprint", None):
        return None
    return publish(game, root=root)
