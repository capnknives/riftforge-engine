"""discord_ooc_links.py -- Discord user ↔ Account mapping for OOC relay.

Players generate a short-lived link code in-game (``account discord link``).
The Discord OOC sidecar bot completes the link when they post ``!link CODE``
in the configured #ooc channel. Links persist in ``.discord_ooc_links.json``
(gitignored) so reboots keep mappings.

Pending codes live in ``.discord_ooc_pending.json`` (also gitignored).
"""

from __future__ import annotations

import json
import os
import secrets
import string
import time
from pathlib import Path

LINKS_NAME = ".discord_ooc_links.json"
PENDING_NAME = ".discord_ooc_pending.json"
CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_LEN = 8
CODE_TTL_SEC = 600  # 10 minutes


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def links_path(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / LINKS_NAME


def pending_path(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / PENDING_NAME


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _normalize_code(raw: str) -> str:
    return str(raw or "").strip().upper().replace("-", "").replace(" ", "")


def _format_code(code: str) -> str:
    """Human-friendly ABCD-EFGH display."""
    c = _normalize_code(code)
    if len(c) != CODE_LEN:
        return c
    return f"{c[:4]}-{c[4:]}"


def _load_links(root=None) -> dict:
    raw = _read_json(links_path(root), {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for discord_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        account = str(entry.get("account") or "").strip()
        if not account:
            continue
        out[str(discord_id)] = {
            "account": account,
            "discord_name": str(entry.get("discord_name") or "").strip(),
            "linked_at": float(entry.get("linked_at") or 0.0),
        }
    return out


def _save_links(links: dict, root=None) -> Path:
    path = links_path(root)
    _write_json(path, links)
    return path


def _load_pending(root=None) -> dict:
    raw = _read_json(pending_path(root), {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def _save_pending(pending: dict, root=None) -> Path:
    path = pending_path(root)
    _write_json(path, pending)
    return path


def _purge_expired_pending(pending: dict) -> dict:
    now = time.time()
    out = {}
    for code, entry in pending.items():
        expires = float(entry.get("expires_at") or 0.0)
        if expires > now:
            out[code] = entry
    return out


def generate_link_code(account_name: str, *, root=None) -> tuple[str, str, float]:
    """Mint a pending link code for ``account_name``.

    Returns ``(display_code, normalized_code, expires_at)``.
    """
    from engine import accounts as accounts_mod

    cleaned, err = accounts_mod.normalize_account_name(account_name)
    if err:
        raise ValueError(err)
    pending = _purge_expired_pending(_load_pending(root))
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    expires_at = time.time() + CODE_TTL_SEC
    pending[code] = {
        "account": cleaned,
        "expires_at": expires_at,
    }
    _save_pending(pending, root)
    return _format_code(code), code, expires_at


def complete_link(
    code: str,
    discord_user_id: str,
    discord_name: str = "",
    *,
    root=None,
) -> tuple[bool, str]:
    """Bind a Discord user to the account behind ``code``."""
    norm = _normalize_code(code)
    if len(norm) != CODE_LEN:
        return False, "Invalid link code format."
    pending = _purge_expired_pending(_load_pending(root))
    entry = pending.get(norm)
    if not entry:
        return False, "That link code is unknown or expired. In-game: account discord link"
    expires = float(entry.get("expires_at") or 0.0)
    if expires <= time.time():
        pending.pop(norm, None)
        _save_pending(pending, root)
        return False, "That link code expired. In-game: account discord link"
    account = str(entry.get("account") or "").strip()
    if not account:
        return False, "That link code is invalid."
    links = _load_links(root)
    # One Discord user → one account; relink overwrites.
    links[str(discord_user_id)] = {
        "account": account,
        "discord_name": str(discord_name or "").strip(),
        "linked_at": time.time(),
    }
    _save_links(links, root)
    pending.pop(norm, None)
    _save_pending(pending, root)
    return True, f"Linked Discord to account {account}."


def unlink_discord_user(discord_user_id: str, *, root=None) -> bool:
    """Remove a Discord mapping. Returns True when one existed."""
    links = _load_links(root)
    key = str(discord_user_id)
    if key not in links:
        return False
    del links[key]
    _save_links(links, root)
    return True


def unlink_account(account_name: str, *, root=None) -> int:
    """Remove every Discord mapping for ``account_name``. Returns count removed."""
    from engine import accounts as accounts_mod

    key = accounts_mod.account_lookup_key(account_name)
    links = _load_links(root)
    removed = 0
    for discord_id, entry in list(links.items()):
        if accounts_mod.account_lookup_key(entry.get("account")) == key:
            del links[discord_id]
            removed += 1
    if removed:
        _save_links(links, root)
    return removed


def lookup_account_for_discord(discord_user_id: str, *, root=None) -> str | None:
    """Return linked account storage name, or None."""
    entry = _load_links(root).get(str(discord_user_id))
    if not entry:
        return None
    account = str(entry.get("account") or "").strip()
    return account or None


def status_for_account(game, account_name: str, *, root=None) -> str:
    """Multi-line status for ``account discord``."""
    from engine import accounts as accounts_mod

    key = accounts_mod.account_lookup_key(account_name)
    links = _load_links(root)
    matched = [
        (did, entry)
        for did, entry in links.items()
        if accounts_mod.account_lookup_key(entry.get("account")) == key
    ]
    if not matched:
        return (
            "Discord: not linked.\r\n"
            "Type account discord link for a one-time code, then in Discord "
            "#ooc post: !link YOUR-CODE"
        )
    lines = ["Discord: linked."]
    for did, entry in matched:
        name = entry.get("discord_name") or did
        lines.append(f"  {name} (id {did})")
    lines.append("Unlink: account discord unlink")
    return "\r\n".join(lines)


def resolve_ooc_face_for_discord(game, account_name: str) -> str:
    """OOC speaker label for a Discord-originated line."""
    from engine import accounts as accounts_mod
    from engine import ooc_channel

    account = accounts_mod.find_account(game, account_name)
    if account is None:
        return str(account_name or "Discord")
    # Prefer an online character on this account.
    for session in list(getattr(game, "sessions", None) or []):
        character = getattr(session, "character", None)
        if character is None:
            continue
        char_acct = (getattr(character, "account", None) or "").strip()
        if accounts_mod.account_lookup_key(char_acct) == accounts_mod.account_lookup_key(
            account.name
        ):
            return ooc_channel.speaker_face_for_character(character, game)
    # Offline: honor account ooc_identity against first roster body.
    for key in list(account.character_keys):
        finder = getattr(game, "find_login_character", None)
        body = finder(key) if callable(finder) else None
        if body is None:
            body = game.find_character(key)
        if body is not None:
            return ooc_channel.speaker_face_for_character(body, game)
    return account.display_name or account.name
