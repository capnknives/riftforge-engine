"""changelog_ledger.py — the single source of truth for changelog ``#N`` ids.

Replaces the "scan markdown, regex-parse a stamp, rewrite files" pipeline
(the old ``engine/changelog_ids.py`` + JSON ``content/changelog_index.json``
combo) with one transactional SQLite ledger: ``content/changelog.db``
(gitignored, generated fresh on every host — live, staging, Azure playtest,
any dev checkout).

Why a ledger and not another JSON file: the old system could have **six**
uncoordinated processes each read-modify-write the same markdown/JSON at
once with zero file locking, which is exactly how duplicate and missing
``#N`` ids happened repeatedly (see ``docs/plans/changelog_system_audit.md``
and the rebuild plan). SQLite's ``BEGIN IMMEDIATE`` transaction gives every
writer real mutual exclusion for free, including across separate OS
processes on the same host — no bespoke lock file needed.

Design:

- ``entries`` table: one row per ``CHANGELOG.d/<slug>.md`` fragment (or one
  synthetic legacy row per old ``CHANGELOG.md`` monolith bullet). ``slug`` is
  the primary key, so re-running ``assign_id`` for a slug that already has a
  row is a safe no-op — that is what makes this resilient to churn (boot,
  idle poll, and copyover can all call ``assign_new_slugs`` freely).
- ``counters`` table: one row per sequence (``player`` / ``staff``) holding
  the next id to hand out. Player-visible bullets and staff-only
  (``[ops]``/``[docs]``/``[easter]``) bullets are numbered independently so
  ``changes`` and ``changes ops`` both stay consecutive on their own axis —
  same two-sequence design the old stamper used, just enforced by a real
  transaction instead of a best-effort scan.
- Ids are **never reassigned** once minted. There is no "renumber" or
  "dedupe" operation in this module on purpose — the old system's ability to
  reassign ids after the fact (to fix a collision) was itself the source of
  "same-looking item, different number" reports on Discord.
- Exception: unpublished mixed-case filename rows (see
  ``reclaim_unpublished_mixed_case_slugs``) were minted but never shown in
  ``changes`` or Discord. Those get dropped once so they can take the next
  ids in line. Posted rows are never touched.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import NamedTuple

_DB_BASENAME = "changelog.db"

# Bullet line matcher. Fragments authored *after* the ledger rebuild never
# carry ``#N`` (the ledger is the only mint point going forward), but every
# fragment authored *before* the rebuild still has the old stamper's ``#N``
# baked into its text, and the frozen ``CHANGELOG.md`` monolith always does.
# This one regex — and the scanner below — must recognize both shapes, or a
# host that boots with an empty ledger silently drops every already-stamped
# bullet instead of importing it (see docs/plans/changelog_system_audit.md,
# "the day-one live regression").
_BULLET_RE = re.compile(
    r"^(\s*-\s+\*\*)"
    r"((?:\[[^\]]+\]\s+)*)"
    r"(?:#(\d+)\s+)?"
    r"((?:\[[^\]]+\]\s+)*)"
    r"(\d{4}-\d{2}-\d{2})"
    r"(?:T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z)?"
    r"(\s+(?:[—–-]|ΓÇö)\s+)"
    r"(.*)$"
)


class _Bullet(NamedTuple):
    """One changelog bullet found on disk — fragment or frozen monolith line."""

    path: str
    line_index: int
    slug: str  # "" for a legacy CHANGELOG.md monolith bullet
    known_id: int  # 0 when the bullet has no existing ``#N`` stamp
    sort_ts: str
    staff_only: bool
    # Post-stamp remainder (tags + headline). Stored on the ledger so
    # ``changes`` can list without re-parsing every fragment at boot.
    summary: str


def canonical_fragment_slug(name_or_path: str) -> str:
    """Ledger / ``changes`` / Discord join key: lowercase stem, no ``.md``.

    Fragment files like ``2026-08-28T22-15-14Z-body-civic-notice.md`` keep
    uppercase ``T`` / ``Z`` on disk (filesystem-safe UTC prefix). The ledger
    always stores the lowercase stem. Every reader must use this helper or
    the join misses, ``changes`` shows no ``#N``, and Discord never posts.
    """
    base = os.path.basename(name_or_path or "")
    if base.lower() == "changelog.md":
        return ""
    if base.lower().endswith(".md"):
        base = base[:-3]
    return base.lower()


def _fragment_slug(path: str) -> str:
    """``CHANGELOG.d/foo.md`` -> ``foo``; empty for the legacy monolith."""
    return canonical_fragment_slug(path)


def _sort_ts_for_date(date: str, time_hms: str | None) -> str:
    """UTC sort key matching ``engine.verbs.basic``'s midnight fallback."""
    if not date:
        return "0001-01-01T00:00:00Z"
    if time_hms:
        return f"{date}T{time_hms}Z"
    return f"{date}T00:00:00Z"


def _fragment_paths(root: str) -> list[str]:
    """Sorted ``CHANGELOG.d/*.md`` paths (skip ``README.md``).

    Shares the memoized listing with ``changelog_fragment_names`` so a
    signature miss (new fragment landed) does not ``os.listdir`` twice —
    once in ``_dir_signature`` and again here — before the bullet scan.
    """
    frag_dir = os.path.join(root, "CHANGELOG.d")
    return [os.path.join(frag_dir, name) for name in changelog_fragment_names(root)]


def _legacy_monolith_slug(bullet: "_Bullet") -> str:
    """Stable synthetic slug for a frozen ``CHANGELOG.md`` monolith bullet.

    Monolith rows have no fragment file (``bullet.slug == ""``); the
    monolith itself stopped growing once ``CHANGELOG.d`` fragments took
    over, so ``line_index`` is a stable identity for it.
    """
    return f"legacy-md-line-{bullet.line_index}"


def _staff_blob_from_match(match: re.Match[str]) -> str:
    """Text ``summary_is_staff_only`` should see: tags plus the real summary.

    ``[ops]`` / ``[docs]`` / ``[easter]`` tags live wherever
    ``CHANGELOG.d/README.md`` documents them — immediately *after* the
    date/em-dash on every fragment authored under that convention (group 8,
    the actual summary text), not just the rarer pre-date position some
    frozen monolith bullets used (groups 2/4). Group 7 is only the em-dash
    separator itself — using it here (a bug this replaced) fed
    ``summary_is_staff_only`` an empty/garbage string and silently dropped
    every fragment-based ``[ops]``/``[docs]``/``[easter]`` bullet into the
    player sequence instead of the staff one.
    """
    pre_date_tags = f"{match.group(2) or ''}{match.group(4) or ''}"
    summary = match.group(8) or ""
    return f"{pre_date_tags}{summary}".strip()


def _scan_bullet_file(path: str, *, unreleased_only: bool) -> list[_Bullet]:
    """Every matching bullet in one file.

    ``unreleased_only`` restricts the scan to the ``## [Unreleased]`` section
    (used for the frozen ``CHANGELOG.md`` monolith, which also has released
    sections below it); fragments have no section headers, so the whole file
    is in scope for them.
    """
    from engine.changelog_audience import summary_is_staff_only

    try:
        # utf-8-sig drops a Windows UTF-8 BOM (PowerShell 5 Set-Content
        # -Encoding utf8). A leading U+FEFF makes _BULLET_RE miss the line,
        # so the fragment never mints and never shows in changes.
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    in_unreleased = not unreleased_only
    out: list[_Bullet] = []
    slug = _fragment_slug(path)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if unreleased_only:
            if stripped.startswith("## [Unreleased]"):
                in_unreleased = True
                continue
            if in_unreleased and stripped.startswith("## "):
                break
            if not in_unreleased:
                continue
        match = _BULLET_RE.match(line.rstrip("\n"))
        if not match:
            continue
        known_id = int(match.group(3)) if match.group(3) else 0
        sort_ts = _sort_ts_for_date(match.group(5), match.group(6))
        staff_only = summary_is_staff_only(_staff_blob_from_match(match), "")
        out.append(
            _Bullet(
                path=path,
                line_index=idx,
                slug=slug,
                known_id=known_id,
                sort_ts=sort_ts,
                staff_only=staff_only,
                summary=_staff_blob_from_match(match),
            )
        )
    return out


def _scan_bullets(root: str) -> list[_Bullet]:
    """Every changelog bullet: frozen monolith + every ``CHANGELOG.d`` fragment.

    Single scanner shared by the live mint path (``assign_new_slugs``) and
    the manual one-time report/import CLI (``tools/changelog_migrate_to_ledger.py``)
    — one bullet-parsing implementation, not two that can drift apart.
    """
    found: list[_Bullet] = []
    changelog_md = os.path.join(root, "CHANGELOG.md")
    if os.path.isfile(changelog_md):
        found.extend(_scan_bullet_file(changelog_md, unreleased_only=True))
    for path in _fragment_paths(root):
        found.extend(_scan_bullet_file(path, unreleased_only=False))
    return found


# Minimum seconds between full ``CHANGELOG.d`` ``os.listdir`` recounts when
# the directory mtime has not changed. Repeated ``changes`` taps (and the
# idle-poll ``assign_new_slugs`` signature check) share this memo so the
# asyncio loop does not walk several thousand filenames on every call.
# In-place edits of an already-shipped fragment do not change the listing
# — those files are frozen prose (see ``_changelog_cache_key``).
SOURCE_LISTING_TTL_S = 12.0

# abs(root) -> (probe_tuple, names_tuple, monotonic_at)
_SOURCE_LISTING_MEMO: dict[str, tuple] = {}


def _stat_mtime_size(path: str) -> tuple[int, int]:
    """``(st_mtime_ns, st_size)`` or ``(0, 0)`` when the path is missing."""
    try:
        st = os.stat(path)
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, 0)


def sources_dir_probe(root: str) -> tuple:
    """Cheap two-stat fingerprint of changelog sources (no ``listdir``).

    Returns ``(frag_dir_stat, changelog_md_stat)`` where each stat is
    ``(mtime_ns, size)``. A directory's own mtime changes when a file
    inside it is added, removed, or renamed (NTFS, overlay2, ext4), which
    is the auto-deploy-overlay case ``changes`` has to notice. In-place
    edits of an existing fragment typically do *not* bump the directory
    mtime — that is why ``changelog_fragment_names`` also has a TTL
    fallback, not a one-shot forever cache.
    """
    return (
        _stat_mtime_size(os.path.join(root, "CHANGELOG.d")),
        _stat_mtime_size(os.path.join(root, "CHANGELOG.md")),
    )


def _listdir_fragment_names(root: str) -> tuple[str, ...]:
    """Sorted ``CHANGELOG.d/*.md`` filenames, excluding README. One listdir."""
    frag_dir = os.path.join(root, "CHANGELOG.d")
    if not os.path.isdir(frag_dir):
        return ()
    names = [
        n for n in os.listdir(frag_dir)
        if n.endswith(".md") and n.lower() != "readme.md"
    ]
    names.sort()
    return tuple(names)


def changelog_fragment_names(root: str, *, probe: tuple | None = None) -> tuple[str, ...]:
    """Sorted fragment filenames, memoized behind dir-mtime + a short TTL.

    Fast path (typical repeated ``changes`` tap): the two-stat probe matches
    the last listing *and* that listing is younger than
    ``SOURCE_LISTING_TTL_S`` — return the remembered name tuple, no
    ``os.listdir``.

    Slow path (listdir): the probe changed (new/removed fragment) *or* the
    TTL elapsed. The TTL is the safety net for filesystems that do not bump
    a directory mtime on every create, so a newly overlaid fragment is
    still picked up within one interval without waiting for copyover.
    """
    cache_key = os.path.abspath(root)
    if probe is None:
        probe = sources_dir_probe(root)
    now = time.monotonic()
    memo = _SOURCE_LISTING_MEMO.get(cache_key)
    if memo is not None:
        last_probe, last_names, last_at = memo
        if last_probe == probe and (now - last_at) < SOURCE_LISTING_TTL_S:
            return last_names
    names = _listdir_fragment_names(root)
    _SOURCE_LISTING_MEMO[cache_key] = (probe, names, now)
    return names


def changelog_source_file_count(root: str, *, probe: tuple | None = None) -> int:
    """How many ``CHANGELOG.d`` fragment files exist (excludes README)."""
    return len(changelog_fragment_names(root, probe=probe))


def _dir_signature(root: str) -> tuple:
    """Cheap staleness signature for the "did anything change" fast path.

    Two ``os.stat`` calls plus a *memoized* fragment listing — no fragment
    file is opened, and repeated calls within ``SOURCE_LISTING_TTL_S`` do
    not ``os.listdir`` the several-thousand-file backlog. A directory's
    own mtime changes whenever a file inside it is added/removed/renamed,
    so this still catches every case that needs a real bullet rescan.
    """
    probe = sources_dir_probe(root)
    count = changelog_source_file_count(root, probe=probe)
    return (probe[0], probe[1], count)


# Per-process cache of the last root scanned successfully — lets a long-lived
# watcher/game process skip the full scan on every idle poll when nothing on
# disk has changed. Keyed by absolute root path so tests using a temp root
# never collide with the real repo.
_SCAN_SIGNATURE_CACHE: dict[str, tuple] = {}
# Same fingerprint persisted in the ledger ``meta`` table so a *new*
# game-child process (every copyover) can skip ``_scan_bullets`` too.
# The in-process cache is empty after ``os._exit`` / watcher respawn;
# without this, Game() re-read every CHANGELOG.d fragment on each Veil.
# v2: force one full rescan after utf-8-sig mint (v1 skip could freeze a
# BOM fragment that never produced a ledger row).
_SCAN_SIGNATURE_META_KEY = "dir_scan_signature_v2"


def reset_source_listing_memo() -> None:
    """Drop in-process listing / scan-signature memos (targeted smokes)."""
    _SOURCE_LISTING_MEMO.clear()
    _SCAN_SIGNATURE_CACHE.clear()


def _encode_scan_signature(sig: tuple) -> str:
    """Stable JSON for the dir fingerprint (tuples become lists)."""
    return json.dumps(sig, separators=(",", ":"))


def _read_persisted_scan_signature(root: str) -> str | None:
    """Ledger-meta copy of the last successful scan fingerprint, or None."""
    conn = connect(root)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            (_SCAN_SIGNATURE_META_KEY,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    value = row["value"]
    return str(value) if value else None


def _write_persisted_scan_signature(root: str, encoded: str) -> None:
    """Remember the fingerprint so the next Game() process can skip."""
    conn = connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SCAN_SIGNATURE_META_KEY, encoded),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _clear_persisted_scan_signature(root: str) -> None:
    """Drop the persisted fingerprint (reclaim forced a remint)."""
    conn = connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM meta WHERE key = ?",
            (_SCAN_SIGNATURE_META_KEY,),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()

# One row per sequence. Player-visible bullets vs. staff-only (``[ops]`` /
# ``[docs]`` / ``[easter]``) bullets are numbered independently.
SEQUENCES = ("player", "staff")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    slug TEXT PRIMARY KEY,
    sequence TEXT NOT NULL CHECK(sequence IN ('player', 'staff')),
    id INTEGER NOT NULL,
    sort_ts TEXT,
    summary TEXT,
    audience TEXT,
    posted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(sequence, id)
);
CREATE TABLE IF NOT EXISTS counters (
    sequence TEXT PRIMARY KEY,
    next_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_sequence_id ON entries(sequence, id);
CREATE INDEX IF NOT EXISTS idx_entries_posted ON entries(sequence, posted);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# One-shot: drop unpublished mixed-case-filename rows so they remint at
# next_id. Players never saw the old numbers (the join missed). Posted rows
# stay put. After this key is set, later mixed-case files mint normally.
RECLAIM_MIXED_CASE_UNPUBLISHED_V1 = "reclaim_mixed_case_unpublished_v1"


def _repo_root_from_here() -> str:
    """Directory that contains ``CHANGELOG.md`` (parent of ``engine/``)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def db_path(root: str | None = None) -> str:
    """Absolute path to the per-host SQLite ledger (gitignored)."""
    base = root or _repo_root_from_here()
    return os.path.join(base, "content", _DB_BASENAME)


def connect(root: str | None = None) -> sqlite3.Connection:
    """Open the ledger, creating the schema if this is a fresh host.

    ``isolation_level=None`` puts the connection in autocommit mode so every
    caller explicitly opens its own ``BEGIN IMMEDIATE`` — that is what makes
    the mint transaction below safe against a second process racing it.
    """
    path = db_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL lets readers (changes / discord poster) run concurrently with a
    # writer's transaction instead of blocking on a single file lock.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_sequence(sequence: str) -> str:
    seq = (sequence or "").strip().lower()
    if seq not in SEQUENCES:
        raise ValueError(f"changelog_ledger: unknown sequence {sequence!r}")
    return seq


def peek_next_id(conn: sqlite3.Connection, sequence: str) -> int:
    """What ``assign_id`` would hand out next, without minting anything."""
    sequence = _normalize_sequence(sequence)
    row = conn.execute(
        "SELECT next_id FROM counters WHERE sequence = ?", (sequence,)
    ).fetchone()
    return int(row["next_id"]) if row else 1


def assign_id(
    conn: sqlite3.Connection,
    slug: str,
    sequence: str,
    *,
    sort_ts: str | None = None,
    summary: str | None = None,
    audience: str | None = None,
) -> int:
    """Return the permanent id for *slug*, minting one if this is new.

    Single mint transaction: if the slug already has a row, this is a
    read-only no-op that returns the existing id (safe to call every boot,
    every idle poll, as many times as you like). Otherwise it reads the
    counter, inserts the row with that id, and bumps the counter — all
    inside one ``BEGIN IMMEDIATE`` so a second process racing this call
    simply blocks until the first commits, then sees the row already exists.
    """
    slug = canonical_fragment_slug(slug)
    if not slug:
        raise ValueError("changelog_ledger: slug is required")
    sequence = _normalize_sequence(sequence)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id FROM entries WHERE slug = ?", (slug,)
        ).fetchone()
        if row is not None:
            conn.execute("COMMIT")
            return int(row["id"])

        counter_row = conn.execute(
            "SELECT next_id FROM counters WHERE sequence = ?", (sequence,)
        ).fetchone()
        next_id = int(counter_row["next_id"]) if counter_row else 1

        conn.execute(
            "INSERT INTO entries "
            "(slug, sequence, id, sort_ts, summary, audience, posted, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (slug, sequence, next_id, sort_ts, summary, audience, _now()),
        )
        conn.execute(
            "INSERT INTO counters (sequence, next_id) VALUES (?, ?) "
            "ON CONFLICT(sequence) DO UPDATE SET next_id = excluded.next_id",
            (sequence, next_id + 1),
        )
        conn.execute("COMMIT")
        return next_id
    except Exception:
        conn.execute("ROLLBACK")
        raise


def insert_with_known_id(
    conn: sqlite3.Connection,
    slug: str,
    sequence: str,
    known_id: int,
    *,
    sort_ts: str | None = None,
    summary: str | None = None,
    audience: str | None = None,
) -> None:
    """Migration-only: insert a historical row at a pre-computed id.

    Callers (the one-time migration script) are responsible for computing a
    collision-free ``known_id`` sequence and for calling ``set_next_id``
    afterward so future ``assign_id`` calls continue past the highest
    historical id. Not for runtime use — runtime code always calls
    ``assign_id`` so the counter and the row are updated atomically together.
    """
    slug = canonical_fragment_slug(slug)
    if not slug:
        raise ValueError("changelog_ledger: slug is required")
    sequence = _normalize_sequence(sequence)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO entries "
            "(slug, sequence, id, sort_ts, summary, audience, posted, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (slug, sequence, int(known_id), sort_ts, summary, audience, _now()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def set_next_id(conn: sqlite3.Connection, sequence: str, next_id: int) -> None:
    """Migration-only: set a sequence's counter after bulk historical inserts."""
    sequence = _normalize_sequence(sequence)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO counters (sequence, next_id) VALUES (?, ?) "
            "ON CONFLICT(sequence) DO UPDATE SET next_id = excluded.next_id",
            (sequence, int(next_id)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _audience_json_for_summary(summary: str) -> str:
    """JSON list of game-package audience tags for a stored summary."""
    from engine.changelog_audience import entry_audience, strip_leading_tags

    tags, _rest = strip_leading_tags(summary or "")
    return json.dumps(sorted(entry_audience(tags)))


def plan_new_assignments(root: str) -> dict[str, list[tuple]]:
    """Compute ``{sequence: [(slug, id, sort_ts, summary, audience), ...]}``.

    Ordering / continuity rule (oldest-first, per sequence):

    1. Sort every bullet by ``(sort_ts, slug, path, line_index)`` — oldest
       ship first. This is what keeps ``#N`` roughly tracking real ship
       chronology; minting in on-disk file-listing order instead of this
       (a regression this function replaced) scrambles ids the moment a
       host's ledger starts from empty with thousands of backlog fragments.
    2. Split into the ``player`` and ``staff`` sequences (same split as
       ``changes`` vs ``changes ops``).
    3. Walk each sequence in that order. A bullet that already carries a
       **unique** ``#N`` stamp in its markdown text keeps that number (this
       is what preserves continuity with numbers already posted to Discord
       / already memorized by players from before the ledger existed). A
       bullet with no stamp, or a stamp that collides with one already
       claimed earlier in the walk, gets the next free id.

    Skips any slug already present in the ledger, so a second run (or the
    live mint path picking up one new fragment) only plans what is new.
    Safe to call against a ledger that already has rows — existing ids are
    never touched or reassigned.
    """
    bullets = _scan_bullets(root)
    already = slug_id_map(root)

    plans: dict[str, list[tuple]] = {"player": [], "staff": []}
    used_ids: dict[str, set[int]] = {"player": set(), "staff": set()}
    next_free: dict[str, int] = {"player": 1, "staff": 1}

    for sequence, existing_id in already.values():
        used_ids[sequence].add(existing_id)
        next_free[sequence] = max(next_free[sequence], existing_id + 1)

    ordered = sorted(
        bullets,
        key=lambda b: (
            b.sort_ts,
            b.slug or _legacy_monolith_slug(b),
            b.path,
            b.line_index,
        ),
    )
    for bullet in ordered:
        slug = bullet.slug or _legacy_monolith_slug(bullet)
        if slug in already:
            continue
        sequence = "staff" if bullet.staff_only else "player"
        candidate = bullet.known_id
        if candidate and candidate not in used_ids[sequence]:
            new_id = candidate
        else:
            new_id = next_free[sequence]
        used_ids[sequence].add(new_id)
        next_free[sequence] = max(next_free[sequence], new_id + 1)
        plans[sequence].append(
            (
                slug,
                new_id,
                bullet.sort_ts,
                bullet.summary,
                _audience_json_for_summary(bullet.summary),
            )
        )

    return plans


def apply_assignments(
    root: str, plans: dict[str, list[tuple]],
) -> int:
    """Insert planned rows and advance each sequence's counter."""
    conn = connect(root)
    inserted = 0
    try:
        for sequence, rows in plans.items():
            for row in rows:
                slug, entry_id, sort_ts = row[0], row[1], row[2]
                summary = row[3] if len(row) > 3 else None
                audience = row[4] if len(row) > 4 else None
                insert_with_known_id(
                    conn,
                    slug,
                    sequence,
                    entry_id,
                    sort_ts=sort_ts,
                    summary=summary,
                    audience=audience,
                )
                inserted += 1
            existing_max = max_id(root, sequence)
            if rows or existing_max:
                set_next_id(conn, sequence, existing_max + 1)
    finally:
        conn.close()
    return inserted


def backfill_missing_prose(root: str | None = None, *, log_prefix: str | None = None) -> int:
    """Fill empty ledger ``summary`` / ``audience`` from markdown (once).

    Older mint rows stored only ``sort_ts``. ``changes`` now lists from the
    ledger, so those columns must be populated. When every row already has
    a summary this is a cheap SQL check and does not walk CHANGELOG.d.
    """
    root = root or _repo_root_from_here()
    conn = connect(root)
    try:
        missing = conn.execute(
            "SELECT slug FROM entries WHERE summary IS NULL OR TRIM(summary) = ''"
        ).fetchall()
        slugs = [row["slug"] for row in missing]
    finally:
        conn.close()
    if not slugs:
        return 0

    bullets_by_slug = {}
    for bullet in _scan_bullets(root):
        slug = bullet.slug or _legacy_monolith_slug(bullet)
        slug = canonical_fragment_slug(slug) or slug
        bullets_by_slug[slug] = bullet

    conn = connect(root)
    updated = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for slug in slugs:
            bullet = bullets_by_slug.get(slug)
            if bullet is None:
                continue
            summary = bullet.summary
            audience = _audience_json_for_summary(summary)
            conn.execute(
                "UPDATE entries SET summary = ?, audience = ? WHERE slug = ?",
                (summary, audience, slug),
            )
            updated += 1
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
    if log_prefix and updated:
        print(
            f"{log_prefix} changelog_ledger backfilled prose for {updated} row(s)",
            flush=True,
        )
    return updated


def _mixed_case_fragment_slugs(root: str) -> list[str]:
    """Canonical slugs for fragment files whose stem is not already lowercase.

    ``2026-08-28T22-15-14Z-body-civic-notice.md`` is the shape that missed
    the ``changes`` / Discord join; ``chargen-birth-year-age.md`` is not.
    """
    out = []
    for name in changelog_fragment_names(root):
        stem = os.path.splitext(name)[0]
        if stem != stem.lower():
            out.append(canonical_fragment_slug(name))
    return out


def reclaim_unpublished_mixed_case_slugs(
    root: str | None = None,
    *,
    log_prefix: str | None = None,
) -> int:
    """Drop unpublished mixed-case-filename rows so they remint at next_id.

    Those ships were minted into the ledger but never shown in ``changes``
    or Discord (filename ``T``/``Z`` vs lowercase ledger slug). Players
    never saw the old numbers. Posted rows stay put.

    One-shot via the ledger ``meta`` table — later mixed-case files mint
    normally at the live counter. Returns how many rows were dropped.
    """
    root = root or _repo_root_from_here()
    conn = connect(root)
    try:
        done = conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            (RECLAIM_MIXED_CASE_UNPUBLISHED_V1,),
        ).fetchone()
        if done is not None:
            return 0

        slugs = _mixed_case_fragment_slugs(root)
        conn.execute("BEGIN IMMEDIATE")
        deleted = 0
        for slug in slugs:
            row = conn.execute(
                "SELECT slug, posted, sequence, id FROM entries WHERE slug = ?",
                (slug,),
            ).fetchone()
            if row is None:
                continue
            if int(row["posted"] or 0):
                continue
            conn.execute("DELETE FROM entries WHERE slug = ?", (slug,))
            deleted += 1
            if log_prefix:
                print(
                    f"{log_prefix} changelog_ledger dropped unpublished "
                    f"#{int(row['id'])} ({row['sequence']}) {slug} for remint",
                    flush=True,
                )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            (RECLAIM_MIXED_CASE_UNPUBLISHED_V1, "1"),
        )
        conn.execute("COMMIT")
        return deleted
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def assign_new_slugs(
    root: str | None = None,
    *,
    log_prefix: str | None = None,
) -> int:
    """Mint ids for every changelog bullet not yet in the ledger.

    This is the **only** place new changelog ids get minted at runtime
    (called from ``Game()`` boot; a defensive second call from the
    watcher's idle poll is safe — every insert is idempotent per slug and
    ids are never reassigned). Covers the frozen ``CHANGELOG.md`` monolith
    plus every ``CHANGELOG.d`` fragment (old ``#N``-stamped ones from
    before the ledger existed *and* new unstamped ones), so a fresh host
    self-heals its entire backlog on first boot with no separate manual
    migration step. Returns how many new ids were minted.

    One-shot: unpublished mixed-case-filename rows are dropped first so
    they take the next ids in line (after ships that already displayed).

    Skips the scan entirely (returning 0) when ``CHANGELOG.d`` and
    ``CHANGELOG.md`` look unchanged since the last successful mint —
    in this process *or* (via ledger ``meta``) the previous game child.
    Copyover respawns a fresh interpreter, so the in-process cache alone
    could not save Veil reloads from re-reading thousands of fragments.
    """
    root = root or _repo_root_from_here()
    reclaimed = reclaim_unpublished_mixed_case_slugs(
        root, log_prefix=log_prefix,
    )
    cache_key = os.path.abspath(root)
    if reclaimed:
        # Dir mtime did not change; force a rescan so dropped slugs remint.
        _SCAN_SIGNATURE_CACHE.pop(cache_key, None)
        try:
            _clear_persisted_scan_signature(root)
        except sqlite3.Error:
            pass

    sig = _dir_signature(root)
    if _SCAN_SIGNATURE_CACHE.get(cache_key) == sig:
        backfill_missing_prose(root, log_prefix=log_prefix)
        return 0
    encoded = _encode_scan_signature(sig)
    try:
        persisted = _read_persisted_scan_signature(root)
    except sqlite3.Error:
        persisted = None
    if persisted == encoded:
        _SCAN_SIGNATURE_CACHE[cache_key] = sig
        backfill_missing_prose(root, log_prefix=log_prefix)
        return 0

    plans = plan_new_assignments(root)
    minted = apply_assignments(root, plans)
    _SCAN_SIGNATURE_CACHE[cache_key] = sig
    try:
        _write_persisted_scan_signature(root, encoded)
    except sqlite3.Error:
        pass

    if log_prefix and minted:
        for sequence, rows in plans.items():
            for packed in rows:
                slug, entry_id = packed[0], packed[1]
                print(
                    f"{log_prefix} changelog_ledger minted #{entry_id} "
                    f"({sequence}) for {slug}",
                    flush=True,
                )
    backfill_missing_prose(root, log_prefix=log_prefix)
    return minted


def get_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    """One ledger row by fragment slug, or None."""
    slug = canonical_fragment_slug(slug)
    if not slug:
        return None
    row = conn.execute(
        "SELECT * FROM entries WHERE slug = ?", (slug,)
    ).fetchone()
    return dict(row) if row is not None else None


def get_by_id(conn: sqlite3.Connection, sequence: str, entry_id: int) -> dict | None:
    """One ledger row by ``(sequence, id)``, or None."""
    sequence = _normalize_sequence(sequence)
    row = conn.execute(
        "SELECT * FROM entries WHERE sequence = ? AND id = ?",
        (sequence, int(entry_id)),
    ).fetchone()
    return dict(row) if row is not None else None


def slug_id_map(root: str | None = None) -> dict[str, tuple[str, int]]:
    """``{slug: (sequence, id)}`` for every ledger row — bulk lookup for readers."""
    conn = connect(root)
    try:
        rows = conn.execute("SELECT slug, sequence, id FROM entries").fetchall()
        return {row["slug"]: (row["sequence"], int(row["id"])) for row in rows}
    finally:
        conn.close()


def list_entries(root: str | None = None, *, sequence: str | None = None) -> list[dict]:
    """All ledger rows, ordered oldest-id-first, optionally filtered by sequence."""
    conn = connect(root)
    try:
        if sequence:
            sequence = _normalize_sequence(sequence)
            rows = conn.execute(
                "SELECT * FROM entries WHERE sequence = ? ORDER BY id",
                (sequence,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entries ORDER BY sequence, id"
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def unposted_player_entries(root: str | None = None) -> list[dict]:
    """Player-visible rows Discord has not posted yet, oldest-id-first.

    Because ids are permanent once minted, "frozen at publish" needs no
    special logic here — whatever id this row holds is the id Discord will
    print, forever.
    """
    conn = connect(root)
    try:
        rows = conn.execute(
            "SELECT * FROM entries WHERE sequence = 'player' AND posted = 0 "
            "ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_posted(root: str | None, slugs: list[str]) -> None:
    """Flip ``posted=1`` for the given slugs in one short transaction."""
    slugs = [
        canonical_fragment_slug(s)
        for s in (slugs or [])
        if s and str(s).strip()
    ]
    slugs = [s for s in slugs if s]
    if not slugs:
        return
    conn = connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "UPDATE entries SET posted = 1 WHERE slug = ?",
            [(slug,) for slug in slugs],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def max_id(root: str | None, sequence: str) -> int:
    """Highest minted id for *sequence*, or 0 when the sequence is empty."""
    conn = connect(root)
    try:
        sequence = _normalize_sequence(sequence)
        row = conn.execute(
            "SELECT MAX(id) AS m FROM entries WHERE sequence = ?", (sequence,)
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0
    finally:
        conn.close()
