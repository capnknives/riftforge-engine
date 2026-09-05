"""Player-typed catalog ids -- spaces and hyphens, never required underscores.

Screen readers spell ``_`` letter by letter (or skip it). Internal storage
may stay snake_case (``no_fixed_address``, ``travel_leash``); anything a
player types as an argument must also accept the spaced form
(``no fixed address``) and the hyphenated form (``no-fixed-address``).

Callers match against catalog keys with :func:`fold_catalog_id` /
:func:`match_catalog_id` / :func:`consume_catalog_prefix`. Player-facing
usage and help should print :func:`spaced_catalog_id`, not the raw id.

This module is engine-generic (no SUPERS imports). Games keep their own
catalogs; they share this fold so ``personality``, ``echo``, ``learn``,
``help``, and the rest stay consistent.
"""

from __future__ import annotations


def fold_catalog_id(raw) -> str:
    """Collapse player-typed text to a snake_case catalog id.

    ``no fixed address`` / ``no-fixed-address`` / ``No_Fixed_Address``
    all become ``no_fixed_address``. Runs of separators collapse. Empty
    or whitespace-only input returns ``""``.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    # Hyphens and underscores both count as spaces so players never have
    # to guess which separator the catalog used.
    for sep in ("-", "_"):
        text = text.replace(sep, " ")
    parts = [part for part in text.split() if part]
    return "_".join(parts)


def spaced_catalog_id(raw) -> str:
    """Player-facing label: underscores and hyphens become spaces.

    ``travel_leash`` / ``one-hand`` -> ``travel leash`` / ``one hand``.
    Use this in usage lines, tips, and confirmations -- never interpolate
    the raw catalog id into something a player might retype.
    """
    text = str(raw or "").replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def match_catalog_id(raw, known):
    """Return the catalog id in ``known`` that matches folded ``raw``.

    Tries, in order: exact (so already-canonical ids stay fast), then the
    folded form, then any known id whose own fold equals the folded input
    (covers catalog keys that still use hyphens). Returns ``None`` when
    nothing matches. ``known`` is any iterable of strings (a dict of ids
    is fine -- we iterate its keys).
    """
    text = str(raw or "").strip().lower()
    if not text:
        return None
    # Dicts are the usual catalog shape -- iterating yields keys.
    if text in known:
        return text
    folded = fold_catalog_id(text)
    if not folded:
        return None
    if folded in known:
        return folded
    for kid in known:
        if fold_catalog_id(kid) == folded:
            return kid
    return None


def consume_catalog_prefix(tokens, known, *, max_tokens=4):
    """Longest prefix of ``tokens`` that folds to a key in ``known``.

    ``echo travel leash town`` must match ``travel_leash`` (two tokens)
    and leave ``town`` as the value -- not the shorter ``travel`` pref.
    Returns ``(matched_id, remaining_tokens)``. On no match,
    ``(None, original tokens)``.

    ``max_tokens`` caps how many words we glue (personality traits are
    three words; four is slack for ``veteran earth traveler``-shaped ids).
    """
    tokens = [str(t) for t in (tokens or ()) if str(t).strip()]
    if not tokens:
        return None, []
    nmax = min(len(tokens), int(max_tokens) or 1)
    for n in range(nmax, 0, -1):
        hit = match_catalog_id(" ".join(tokens[:n]), known)
        if hit is not None:
            return hit, tokens[n:]
    return None, tokens
