"""
cursor_automations.py -- catalog of the three Cursor Automations the game POSTs to.

The game never creates automations (dashboard-only). This module is the
**in-repo SoT** for how GM verbs map to webhook env vars and setup docs, so
``squashbugs`` / ``squashsuggest`` / ``gm diaglog analyze`` stay aligned with
``.cursor/automations/``.

Secrets stay in host ``.env`` (never committed). Use
``tools/live_update_webhook_env.py`` to push configured keys to live.
"""

from __future__ import annotations

import os


# Three automations -- keep order: bugs, suggestions, lag diag.
AUTOMATIONS = (
    {
        "slug": "bug-fixer",
        "name": "In-game bug report fixer",
        "kind": "bug",
        "gm_verbs": ("squashbug", "squashbugs"),
        "url_env": "CURSOR_BUG_WEBHOOK_URL",
        "auth_env": "CURSOR_BUG_WEBHOOK_AUTH",
        "setup_doc": ".cursor/automations/SETUP.md",
        "instructions": (
            ".cursor/automations/in-game-bug-report-fixer.INSTRUCTIONS.md"
        ),
    },
    {
        "slug": "suggestion-implementer",
        "name": "In-game suggestion implementer",
        "kind": "suggest",
        "gm_verbs": ("sendsuggest", "squashsuggest"),
        "url_env": "CURSOR_SUGGESTION_WEBHOOK_URL",
        "auth_env": "CURSOR_SUGGESTION_WEBHOOK_AUTH",
        "setup_doc": (
            ".cursor/automations/in-game-suggestion-implementer.SETUP.md"
        ),
        "instructions": (
            ".cursor/automations/in-game-suggestion-implementer.INSTRUCTIONS.md"
        ),
    },
    {
        "slug": "lag-diag-analyzer",
        "name": "Lag diag analyzer",
        "kind": "lag_diag",
        "gm_verbs": ("gm diaglog analyze",),
        "url_env": "RIFTFORGE_DIAG_WEBHOOK_URL",
        "auth_env": "RIFTFORGE_DIAG_WEBHOOK_AUTH",
        "setup_doc": ".cursor/automations/lag-diag-analyzer.SETUP.md",
        "instructions": (
            ".cursor/automations/lag-diag-analyzer.INSTRUCTIONS.md"
        ),
    },
)


def _url_tail(url: str) -> str:
    """Last path segment of a webhook URL (automation uuid), or empty."""
    text = (url or "").strip().rstrip("/")
    if not text:
        return ""
    return text.rsplit("/", 1)[-1]


def env_status(entry: dict) -> dict:
    """Return config status for one automation (no secret values)."""
    url = os.environ.get(entry["url_env"], "").strip()
    auth = os.environ.get(entry["auth_env"], "").strip()
    return {
        "slug": entry["slug"],
        "name": entry["name"],
        "kind": entry["kind"],
        "gm_verbs": entry["gm_verbs"],
        "url_env": entry["url_env"],
        "auth_env": entry["auth_env"],
        "url_set": bool(url),
        "auth_set": bool(auth),
        "url_tail": _url_tail(url) if url else "",
        "ready": bool(url) and bool(auth),
        "setup_doc": entry["setup_doc"],
    }


def all_status() -> list:
    """Status dicts for every catalogued automation."""
    return [env_status(entry) for entry in AUTOMATIONS]


def status_lines() -> list:
    """Plain lines for GM sheets / docker logs (no secrets)."""
    lines = [
        "Cursor automations (webhook env -- never commit secrets):",
    ]
    for st in all_status():
        verbs = " / ".join(st["gm_verbs"])
        if st["ready"]:
            state = f"ready (…/{st['url_tail']})"
        elif st["url_set"] and not st["auth_set"]:
            state = "URL set, AUTH missing (HTTP 401)"
        elif not st["url_set"]:
            state = f"UNSET ({st['url_env']})"
        else:
            state = "incomplete"
        lines.append(f"  [{st['kind']}] {st['name']}: {state}")
        lines.append(f"    GM: {verbs}")
    return lines


def webhook_env_keys() -> tuple:
    """All URL/AUTH env key names the live-update helper should copy."""
    keys = []
    for entry in AUTOMATIONS:
        keys.append(entry["url_env"])
        keys.append(entry["auth_env"])
    return tuple(keys)
