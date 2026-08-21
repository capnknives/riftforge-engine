"""Player-voice prose helpers for CHANGELOG fragments and in-game ``changes``.

Agents often ship RST-style doubled ticks (``gm on``), file paths, and internal
keys. Players read the raw text in telnet — no markdown renderer. This module
normalizes prose for display and gates new fragments in CI.
"""

from __future__ import annotations

import re

# Doubled backticks from Python docstrings / agent rules — never player-facing.
_DOUBLE_TICK_PAIR_RE = re.compile(r"``([^`]*)``")
# Orphan `` (broken markdown like ``gm on` or help foo``).
_ORPHAN_DOUBLE_TICK_RE = re.compile(r"``")

# Single backticks around repo paths, modules, or doc paths.
_PATHY_TICK_RE = re.compile(
    r"`([^`]*(?:supers|engine|tools|docs|content|\.cursor)/[^`]*)`"
    r"|`([^`]*\.(?:py|md|mdc|json|yml|yaml))`",
    re.I,
)

# Internal storage keys and env vars agents wrap in ticks.
_TECH_TICK_RE = re.compile(
    r"`([^`]*(?:gmspirit:|husk:|gm_away|DISCORD_[A-Z0-9_]+)[^`]*)`",
    re.I,
)

# Any remaining single-backtick spans (verbs, help topics, broken markdown).
_SINGLE_TICK_RE = re.compile(r"`([^`]+)`")

# Bare path fragments outside ticks (strip, do not fail CI on slash pairs).
_PATHY_BARE_RE = re.compile(
    r"\b(?:supers|engine|tools|docs|content)/[a-z0-9_./-]+",
    re.I,
)

# CI violation patterns on raw fragment text (before normalize).
_VIOLATION_DOUBLE_TICK = _ORPHAN_DOUBLE_TICK_RE
_VIOLATION_PATHY_TICK = re.compile(
    r"`[^`]*(?:supers|engine|tools|docs|content|\.cursor)/[^`]*`"
    r"|`[^`]*\.(?:py|md|mdc|json|yml|yaml)`",
    re.I,
)
_VIOLATION_TECH_TICK = _TECH_TICK_RE


def normalize_changelog_prose(text: str) -> str:
    """Flatten ticks and drop path noise — plain player-readable sentences."""
    if not text:
        return ""
    s = str(text)
    s = _DOUBLE_TICK_PAIR_RE.sub(r"\1", s)
    s = _ORPHAN_DOUBLE_TICK_RE.sub("", s)
    s = _PATHY_TICK_RE.sub(
        lambda m: (m.group(1) or m.group(2) or "").strip(),
        s,
    )
    s = _TECH_TICK_RE.sub(r"\1", s)
    s = _SINGLE_TICK_RE.sub(r"\1", s)
    s = _PATHY_BARE_RE.sub("", s)
    s = s.replace("`", "")
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;—-")
    return s


def prose_violations(text: str) -> list[str]:
    """Human-readable violation labels for *text* (used by CI audit)."""
    if not text:
        return []
    labels: list[str] = []
    if _VIOLATION_DOUBLE_TICK.search(text):
        labels.append("doubled backticks (``)")
    if _VIOLATION_PATHY_TICK.search(text):
        labels.append("file path or extension in backticks")
    if _VIOLATION_TECH_TICK.search(text):
        labels.append("internal key or env var in backticks")
    return labels


def scan_fragment_lines(lines: list[str]) -> list[tuple[int, str, list[str]]]:
    """Return (line_no, line_text, violation_labels) for non-empty lines."""
    hits: list[tuple[int, str, list[str]]] = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        viol = prose_violations(line)
        if viol:
            hits.append((i, line.rstrip(), viol))
    return hits
