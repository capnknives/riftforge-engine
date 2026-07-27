"""Runtime staff toggles to disable player verbs (``gm disable <verb>``).

Disabled verbs are stored on ``game.disabled_verbs`` (in-memory for this
process). Copyover keeps the set; a full restart clears it.
"""


# Verbs that must never be disabled -- staff still need help/quit/gm.
_DISABLE_PROTECTED = frozenset({
    "gm",
    "help",
    "commands",
    "quit",
    "logout",
    "bug",
    "suggest",
})


def ensure_disabled_set(game):
    """Return ``game.disabled_verbs``, creating an empty set if missing."""
    disabled = getattr(game, "disabled_verbs", None)
    if disabled is None:
        disabled = set()
        game.disabled_verbs = disabled
    return disabled


def is_protected(verb):
    """True when staff must not be allowed to disable this verb."""
    return (verb or "").strip().lower() in _DISABLE_PROTECTED


def is_disabled(game, verb):
    """True when ``verb`` is currently disabled for everyone."""
    low = (verb or "").strip().lower()
    if not low:
        return False
    disabled = getattr(game, "disabled_verbs", None)
    return bool(disabled) and low in disabled


def format_status(game):
    """Human-readable list of disabled verbs for bare ``gm disable``."""
    names = sorted(ensure_disabled_set(game))
    if not names:
        return "No commands are disabled."
    lines = ["Disabled commands:"]
    lines.extend(f"  {name}" for name in names)
    lines.append("(type gm disable <verb> again to re-enable)")
    return "\r\n".join(lines)


def toggle(game, verb, commands):
    """Toggle one verb off/on. Returns (ok, message_for_player).

    ``commands`` is the live dispatch table (``commands.COMMANDS``) so we
    only accept real verbs. ``list`` is a meta arg that shows status.
    """
    low = (verb or "").strip().lower()
    if not low:
        return (
            False,
            "Usage: gm disable <command>  (toggle; type again to re-enable)",
        )
    if low == "list":
        return True, format_status(game)
    if low not in commands:
        return (
            False,
            f"Unknown command '{low}'. Try 'commands' for the verb list.",
        )
    if is_protected(low):
        return False, f"'{low}' cannot be disabled."

    disabled = ensure_disabled_set(game)
    if low in disabled:
        disabled.discard(low)
        return True, f"Re-enabled '{low}'."
    disabled.add(low)
    return (
        True,
        f"Disabled '{low}' for everyone. "
        f"Type 'gm disable {low}' again to re-enable.",
    )
