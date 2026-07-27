"""
char_cnum.py -- Character CNUM helpers (letter prefix + 5 digits).

A CNUM is a staff-facing id like ``CR00001`` derived from a character's
**given name** (first letter + third letter, both uppercase, plus a
zero-padded sequence under that prefix). Short names (fewer than three
letters) repeat the last letter (``Jo`` → ``JO00001``).

Same shape as room vnums (``engine/room_vnum.py``) but a separate
namespace so room and character ids never collide in staff tooling.
Engine-pure: no ``supers`` imports.
"""

from __future__ import annotations

import re

# Human form: two A–Z letters + exactly five decimal digits.
_CNUM_RE = re.compile(r"^([A-Z]{2})(\d{5})$")

# Fallback when the given name has no A–Z letters at all.
_FALLBACK_PREFIX = "XX"

# Per-prefix sequence ceiling (5 digits).
_MAX_SEQ = 99999


def cnum_prefix(given_name: str) -> str:
    """First + third A–Z letters of ``given_name``, both uppercase.

    Fewer than three letters → repeat the last letter (``Jo`` → ``JO``,
    ``A`` → ``AA``). No letters → ``XX``.
    """
    letters = [
        ch.upper()
        for ch in str(given_name or "")
        if ch.isalpha() and ch.isascii() and "A" <= ch.upper() <= "Z"
    ]
    if not letters:
        return _FALLBACK_PREFIX
    first = letters[0]
    if len(letters) >= 3:
        third = letters[2]
    else:
        third = letters[-1]
    return first + third


def format_cnum(prefix: str, n: int) -> str:
    """Build ``CR00001`` from a two-letter prefix and sequence number."""
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not pref.isalpha() or not pref.isascii():
        raise ValueError(f"cnum prefix must be two A–Z letters, got {prefix!r}")
    if not isinstance(n, int) or n < 1 or n > _MAX_SEQ:
        raise ValueError(f"cnum sequence must be 1..{_MAX_SEQ}, got {n!r}")
    return f"{pref}{n:05d}"


def parse_cnum(s) -> tuple[str, int] | None:
    """Return ``(prefix, n)`` for a valid CNUM string, else ``None``."""
    if s is None:
        return None
    text = str(s).strip().upper()
    match = _CNUM_RE.fullmatch(text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def validate_cnum(s) -> str:
    """Normalize and validate; raise ``ValueError`` if malformed."""
    parsed = parse_cnum(s)
    if parsed is None:
        raise ValueError(
            f"invalid character CNUM {s!r} -- expected two A–Z letters + "
            f"5 digits (e.g. CR00001)"
        )
    prefix, n = parsed
    return format_cnum(prefix, n)


def next_cnum(prefix: str, taken: set[str]) -> str:
    """Allocate the next free ``PREFIX#####`` under ``prefix``.

    ``taken`` holds already-used CNUM strings (any casing; compared upper).
    Raises ``ValueError`` if the 5-digit space is exhausted.
    """
    pref = str(prefix or "").strip().upper()
    if len(pref) != 2 or not all("A" <= ch <= "Z" for ch in pref):
        raise ValueError(f"cnum prefix must be two A–Z letters, got {prefix!r}")
    used = {str(v).strip().upper() for v in (taken or ()) if v}
    for n in range(1, _MAX_SEQ + 1):
        candidate = format_cnum(pref, n)
        if candidate not in used:
            return candidate
    raise ValueError(
        f"no free CNUM left under prefix {pref!r} (1..{_MAX_SEQ} exhausted)"
    )


def collect_taken_cnums(characters) -> set[str]:
    """Gather validated CNUM strings from Character objects."""
    taken: set[str] = set()
    for char in characters or ():
        raw = getattr(char, "cnum", None)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            taken.add(validate_cnum(raw))
        except ValueError:
            continue
    return taken


def allocate_cnum(given_name: str, *, taken: set[str]) -> str:
    """Derive prefix from given name and return the next free CNUM."""
    prefix = cnum_prefix(given_name)
    return next_cnum(prefix, taken)
