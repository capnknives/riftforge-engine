"""In-game ``changes`` audience tags and staff-only heuristics.

Fragments stamp leading tags on the summary (after the date em dash in the
markdown lead-in):

- ``[ops]`` / ``[docs]`` — maintainer or doc-only ships; hidden from players.
- ``[easter]`` / ``[easteregg]`` — hidden homestead / franchise easter-egg
  spoilers; staff-only like ``[ops]`` (``changes ops``).
- ``[supers]``, ``[basegame]``, ``[engine]``, ``[classic]`` — which game
  package should list the bullet.

Untagged bullets default to SUPERS. When agents forget ``[ops]``, the same
heuristics as ``tools/changelog_player_pass.py`` still hide obvious repo /
builder / Area Studio noise, and staff playtest verbs such as ``dothepit``,
from non-GM players while keeping real bug-fix blurbs that mention peels or
autodeploy in passing.
"""

from __future__ import annotations

import re

# Leading ``[tag]`` tokens at the start of a parsed summary line.
LEADING_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*")

GAME_AUDIENCE_TAGS = frozenset({"supers", "basegame", "engine", "classic"})
STAFF_ONLY_TAGS = frozenset({"ops", "docs", "easter", "easteregg"})
KNOWN_DISPLAY_TAGS = STAFF_ONLY_TAGS | GAME_AUDIENCE_TAGS

# Summary / body hints: maintainer or agent work, not player news.
OPS_SUMMARY_HINT_RE = re.compile(
    r"(?i)"
    r"(\bREADME\b|\bAGENTS\.md\b|docs/plans/|\.mdc\b|hard rules?\s+\d|"
    r"always-apply rule|digests synced|PR handoff|worktree|"
    r"CHANGELOG\.d/README|tools/changelog|Cursor Automations|GitHub Actions|"
    r"codebase health|two-repo purity|plans index|instruction map|"
    r"smoke[- ]green|engine[- ]only|verb purity|Phase \d+ \(engine|"
    r"agent(s)? own|agent(s)? stamp|maintainer merges|merge-method|"
    r"schedule SoT|lore silence rule|parked stays|live-ssh provisioned|"
    r"gateway must survive rule|feature-worktree|play-checkout rule|"
    r"targeted-smoke rule|clean-working-tree rule|semantic-index|"
    r"instruction harden|copilot-instructions|\.cursor/rules/|"
    r"\barea studio\b|builder audit|builder \(wave|jobs builder|"
    r"quest builder|vendor_stock builder|content builder|schema completion|"
    r"\bdothepit\b)",
)

# Drop sentences that read like dev notes / implementation logs.
_DEV_SENTENCE_RE = re.compile(
    r"(?i)"
    r"(smoke assertion|verified via|intentionally not added|"
    r"no code,? no math|content-only|docs/|\.py\b|\.mdc\b|\.md\b|"
    r"supers/|engine/|tools/|command_support|npc_do\b|"
    r"origin/main|parallel PR|squash-merge|CHANGELOG\.d|"
    r"cadence_audit|balance_sim|needs_timing|"
    r"SYSTEMS_DESIGN|COMBAT_LEXICON|§\d|"
    r"\{brace\}|end-to-end check|targeted check|"
    r"import-time|file_index|_public_label|_resolve_|"
    r"get_\w+_phase|render_\w+|build_brief|"
    r"SQLite meta|IntegrityError|Session\.run|"
    r"watch_and_run|auto_deploy|deploy pipeline|"
    r"soft WARN|not a pass/fail|"
    r"Digests \+|PR template|Unreleased backfill)",
)

# Keep sentences that clearly help a player or on-duty GM.
_PLAYER_SENTENCE_RE = re.compile(
    r"(?i)"
    r"(help \w+|type `|new verb|you can |players can |staff can |"
    r"logging in|log in|combat |fight |hunt |travel |buy |sell |"
    r"introduce|greet|who |look |say |tell |"
    r"Winchester|Sam|Dean|Buffy|vampire|hunter|haunt |"
    r"gateway |telnet |room |Echo |pack |duty |"
    r"see `help |See: help )",
)


def strip_leading_tags(summary):
    """Strip leading ``[tag]`` tokens; return ``(tags, remainder)``."""
    text = str(summary or "").lstrip()
    tags = []
    while True:
        match = LEADING_TAG_RE.match(text)
        if not match:
            break
        tags.append(match.group(1).strip().lower())
        text = text[match.end():]
    return tags, text


def display_summary(summary):
    """Summary text for ``changes`` listings — no leading audience tags."""
    _tags, remainder = strip_leading_tags(summary)
    return remainder.strip()


def audience_tag_labels(tags, *, is_gm):
    """Bracket labels for GM ``changes`` listings (``[ops]``, ``[basegame]``, …).

    Players never see audience tags — filtering uses the raw summary. GMs see
    the tags in the line prefix (after ``[id]``) so staff can tell ops/repo
    ships from player news without repeating the tag in the summary text.
    """
    if not is_gm:
        return []
    labels = []
    for tag in tags:
        key = str(tag).strip().lower()
        if key in KNOWN_DISPLAY_TAGS:
            labels.append(f"[{key}]")
    return labels


def entry_audience(tags):
    """Return which ``RIFTFORGE_GAME`` values should list this bullet."""
    game_tags = [t for t in tags if t in GAME_AUDIENCE_TAGS]
    if not game_tags:
        return {"supers"}
    audience = set()
    for tag in game_tags:
        if tag == "engine":
            audience.update({"basegame", "classic", "none"})
        else:
            audience.add(tag)
    return audience


def _sentences(text):
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def summary_is_staff_only(summary, body=""):
    """Whether a bullet is maintainer-only (explicit tag or heuristic)."""
    tags, clean_summary = strip_leading_tags(summary)
    if any(t in STAFF_ONLY_TAGS for t in tags):
        return True
    if OPS_SUMMARY_HINT_RE.search(clean_summary):
        return True
    blob = f"{clean_summary} {body}"
    player_bits = [
        s for s in _sentences(body)
        if _PLAYER_SENTENCE_RE.search(s) and not _DEV_SENTENCE_RE.search(s)
    ]
    if OPS_SUMMARY_HINT_RE.search(blob) and not player_bits:
        return True
    return False


def entry_body_for_heuristics(entry):
    """Flatten continuation lines for staff-only heuristics (no bold lead)."""
    parts = list(entry.get("full") or [])
    if not parts:
        return ""
    first = parts[0]
    bold = re.match(r"\*\*(.+?)\*\*(.*)$", first, re.DOTALL)
    if bold:
        tail = bold.group(2).strip()
        rest = [tail] if tail else []
    elif first.startswith("**"):
        rest = [first[2:].lstrip()]
    else:
        rest = [first]
    rest.extend(p.strip() for p in parts[1:] if p.strip())
    return " ".join(rest).strip()


def entry_is_staff_only(entry):
    """Whether *entry* is hidden from non-GM ``changes`` viewers."""
    summary = entry.get("summary") or ""
    return summary_is_staff_only(summary, entry_body_for_heuristics(entry))


def visible_to_viewer(entry, active_game, is_gm):
    """Whether *entry* belongs in ``changes`` for this game + staff rank."""
    tags, _ = strip_leading_tags(entry.get("summary") or "")
    if not is_gm and entry_is_staff_only(entry):
        return False
    return active_game in entry_audience(tags)
