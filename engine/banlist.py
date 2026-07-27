"""
banlist.py -- name + IP denylist for login (stdlib JSON beside the DB).

Staff ``ban`` / ``unban`` / ``banlist`` verbs mutate this file. Login in
``engine/connection.py`` consults it before attaching a character so a
banned name or client host never reaches play.

File shape (``banlist.json`` next to the world DB / report_dir)::

    {
      "names": ["BadActor"],
      "ips": ["1.2.3.4"]
    }

Names are compared case-insensitively. Empty lists are fine. Corrupt or
missing files fail closed to "not banned" so a bad write never bricks
login for everyone -- staff can ``banlist`` and re-add.
"""

from __future__ import annotations

import json
import os


# Default filename next to riftforge.db / report_dir.
BANLIST_FILENAME = "banlist.json"


def banlist_path(game):
    """Absolute path for this game's banlist JSON.

    Prefer ``game.report_dir`` (same folder as bug_reports.log) so Docker
    bind-mounts keep bans with the live data. Fall back to ``.``.
    """
    base = getattr(game, "report_dir", None) or "."
    return os.path.join(base, BANLIST_FILENAME)


def load_banlist(game):
    """Return ``{"names": [...], "ips": [...]}`` (always lists of str)."""
    path = banlist_path(game)
    empty = {"names": [], "ips": []}
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, TypeError):
        # Corrupt file -- do not lock the world out.
        return empty
    if not isinstance(raw, dict):
        return empty
    names = raw.get("names") or []
    ips = raw.get("ips") or []
    return {
        "names": [str(n) for n in names if n],
        "ips": [str(i) for i in ips if i],
    }


def save_banlist(game, data):
    """Atomically write the banlist JSON (temp file + replace)."""
    path = banlist_path(game)
    payload = {
        "names": list(data.get("names") or []),
        "ips": list(data.get("ips") or []),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def is_name_banned(game, name):
    """True when ``name`` matches a banned login key (case-insensitive)."""
    if not name:
        return False
    needle = str(name).strip().lower()
    data = load_banlist(game)
    for banned in data["names"]:
        if banned.strip().lower() == needle:
            return True
    return False


def is_ip_banned(game, ip):
    """True when ``ip`` exactly matches a banned host string."""
    if not ip:
        return False
    needle = str(ip).strip()
    data = load_banlist(game)
    for banned in data["ips"]:
        if banned.strip() == needle:
            return True
    return False


def is_banned(game, *, name=None, ip=None):
    """True when either the login name or client IP is on the denylist."""
    if name and is_name_banned(game, name):
        return True
    if ip and is_ip_banned(game, ip):
        return True
    return False


def add_ban(game, *, name=None, ip=None):
    """Add a name and/or IP. Returns (ok, message)."""
    data = load_banlist(game)
    changed = False
    notes = []
    if name:
        clean = str(name).strip()
        if not clean:
            return False, "Empty name."
        # Case-insensitive de-dupe.
        existing = {n.lower() for n in data["names"]}
        if clean.lower() in existing:
            notes.append(f"name '{clean}' already banned")
        else:
            data["names"].append(clean)
            notes.append(f"banned name '{clean}'")
            changed = True
    if ip:
        clean_ip = str(ip).strip()
        if not clean_ip:
            return False, "Empty IP."
        if clean_ip in data["ips"]:
            notes.append(f"IP '{clean_ip}' already banned")
        else:
            data["ips"].append(clean_ip)
            notes.append(f"banned IP '{clean_ip}'")
            changed = True
    if not name and not ip:
        return False, "Ban a name and/or an IP."
    if changed:
        save_banlist(game, data)
    return True, "; ".join(notes) + "."


def remove_ban(game, *, name=None, ip=None):
    """Remove a name and/or IP. Returns (ok, message)."""
    data = load_banlist(game)
    notes = []
    changed = False
    if name:
        clean = str(name).strip().lower()
        kept = [n for n in data["names"] if n.strip().lower() != clean]
        if len(kept) == len(data["names"]):
            notes.append(f"name '{name}' was not banned")
        else:
            data["names"] = kept
            notes.append(f"unbanned name '{name}'")
            changed = True
    if ip:
        clean_ip = str(ip).strip()
        kept_ips = [i for i in data["ips"] if i.strip() != clean_ip]
        if len(kept_ips) == len(data["ips"]):
            notes.append(f"IP '{ip}' was not banned")
        else:
            data["ips"] = kept_ips
            notes.append(f"unbanned IP '{ip}'")
            changed = True
    if not name and not ip:
        return False, "Unban a name and/or an IP."
    if changed:
        save_banlist(game, data)
    return True, "; ".join(notes) + "."


def format_banlist(game):
    """Human-readable banlist for the ``banlist`` verb."""
    data = load_banlist(game)
    lines = ["Banlist:", f"  File: {banlist_path(game)}"]
    if data["names"]:
        lines.append("  Names:")
        for n in sorted(data["names"], key=str.lower):
            lines.append(f"    {n}")
    else:
        lines.append("  Names: (none)")
    if data["ips"]:
        lines.append("  IPs:")
        for i in sorted(data["ips"]):
            lines.append(f"    {i}")
    else:
        lines.append("  IPs: (none)")
    return "\r\n".join(lines)
