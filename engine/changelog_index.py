"""Pre-built JSON index for in-game ``changes`` (fast load).

Authoring stays in ``CHANGELOG.md`` / ``CHANGELOG.d/*.md``. Maintainers rebuild
``content/changelog_index.json`` via ``tools/build_changelog_index.py``
locally; boot and auto-deploy rebuild when stale.
Feature PRs should not commit the index — it conflicts on bundled merges.
"""

from __future__ import annotations

import json
import os

# Bump when the on-disk JSON shape changes.
# v2: list metadata only; ``full`` bodies lazy-load from CHANGELOG.d (Option A).
INDEX_VERSION = 2

# Fields kept in the compiled index (``changes detail`` loads ``full`` on demand).
_INDEX_LIST_KEYS = frozenset({
    "category",
    "id",
    "date",
    "sort_ts",
    "summary",
    "file_index",
    "slug",
    "audience",
    "staff_only",
})

# Git-tracked compiled feed (auto-deploy ships it to live with code).
INDEX_REL_PATH = os.path.join("content", "changelog_index.json")


def index_path(repo_root: str) -> str:
    """Absolute path to the compiled changelog index under *repo_root*."""
    return os.path.join(repo_root, INDEX_REL_PATH)


def signature_to_json(repo_root: str, sig: tuple) -> list:
    """Serialize a ``_changelog_signature`` tuple for stable JSON compare."""
    out = []
    for path, mtime_ns, size in sig:
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        out.append([rel, mtime_ns, size])
    return out


def load_index_file(path: str) -> list | None:
    """Load pre-parsed entries from *path* (runtime fast path).

    Callers should invalidate when the index file's ``mtime`` / size changes.
    Full ``source_signature`` verification is reserved for
    ``tools/build_changelog_index.py --check`` — not per ``changes`` tap.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if int(payload.get("version") or 0) != INDEX_VERSION:
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    return entries


def try_load_index(repo_root: str, sig: tuple) -> list | None:
    """Return entries when the on-disk index matches *sig* (CI / builder)."""
    path = index_path(repo_root)
    entries = load_index_file(path)
    if entries is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if payload.get("source_signature") != signature_to_json(repo_root, sig):
        return None
    return entries


def changelog_source_file_count(repo_root: str) -> int:
    """Count ``CHANGELOG.md`` plus ``CHANGELOG.d/*.md`` (excludes README)."""
    count = 0
    if os.path.isfile(os.path.join(repo_root, "CHANGELOG.md")):
        count += 1
    frag_dir = os.path.join(repo_root, "CHANGELOG.d")
    if os.path.isdir(frag_dir):
        for name in os.listdir(frag_dir):
            if name.endswith(".md") and name.lower() != "readme.md":
                count += 1
    return count


def index_signature_file_count(repo_root: str) -> int | None:
    """How many source files the compiled index was built from (or None)."""
    path = index_path(repo_root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    sig = payload.get("source_signature")
    if not isinstance(sig, list):
        return None
    return len(sig)


def index_likely_stale(repo_root: str) -> bool:
    """Cheap runtime staleness probe — catches forgot-to-rebuild index ships.

    Compares live source file count to the count baked into
    ``content/changelog_index.json``. New ``CHANGELOG.d`` fragments change the
    count without touching the index file, which is exactly how Aug 2026
    ships went missing from ``changes``.
    """
    live_count = changelog_source_file_count(repo_root)
    indexed_count = index_signature_file_count(repo_root)
    if indexed_count is None:
        return True
    return live_count != indexed_count


def _index_mtime_ns(repo_root: str) -> int:
    """Nanosecond mtime of the compiled index, or 0 when missing."""
    path = index_path(repo_root)
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def sources_newer_than_index(repo_root: str) -> bool:
    """True when any CHANGELOG source file is newer than the compiled index.

    Catches in-place fragment edits that do not change the file count. Used
    on boot and deploy (once per sync), not on every ``changes`` tap.
    """
    index_mtime = _index_mtime_ns(repo_root)
    if index_mtime <= 0:
        return True
    main_path = os.path.join(repo_root, "CHANGELOG.md")
    try:
        if os.stat(main_path).st_mtime_ns > index_mtime:
            return True
    except OSError:
        pass
    frag_dir = os.path.join(repo_root, "CHANGELOG.d")
    if not os.path.isdir(frag_dir):
        return False
    for name in os.listdir(frag_dir):
        if not name.endswith(".md") or name.lower() == "readme.md":
            continue
        try:
            if os.stat(os.path.join(frag_dir, name)).st_mtime_ns > index_mtime:
                return True
        except OSError:
            continue
    return False


def index_needs_rebuild(repo_root: str) -> bool:
    """Whether markdown sources outran ``content/changelog_index.json``."""
    return index_likely_stale(repo_root) or sources_newer_than_index(repo_root)


def rebuild_index_from_sources(repo_root: str) -> int:
    """Parse CHANGELOG sources and rewrite the compiled index."""
    from engine.verbs.basic import (
        _changelog_signature,
        _load_changelog_entries_from_sources,
    )

    entries = _load_changelog_entries_from_sources(repo_root)
    sig = _changelog_signature(repo_root)
    write_index(repo_root, sig, entries)
    return len(entries)


def should_mint_ids_on_boot() -> bool:
    """Mint ``#N`` on live/copyover ``Game()`` boot; skip host smokes.

    Smokes and ``tools/local_ci.py`` share the real ``CHANGELOG.d`` tree
    (temp SQLite only). Minting there dirties developer checkouts — a
    pre-push boot probe already restamped five live fragments that way.

    Live/staging Docker sets ``RIFTFORGE_GATEWAY=1`` on the game child
    (and overlay ``boot_probe`` inside compose). That is the copyover
    guaranteed mint. Opt in on a non-gateway boot with
    ``RIFTFORGE_CHANGELOG_MINT=1``.
    """
    import sys

    raw = (os.environ.get("RIFTFORGE_SMOKE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return False
    argv0 = os.path.basename(sys.argv[0] or "").lower()
    if "smoke" in argv0 or "local_ci" in argv0:
        return False
    if "smoke_test" in sys.modules:
        return False
    mint = (os.environ.get("RIFTFORGE_CHANGELOG_MINT") or "").strip().lower()
    if mint in ("0", "false", "no", "off"):
        return False
    if mint in ("1", "true", "yes", "on"):
        return True
    gateway = (os.environ.get("RIFTFORGE_GATEWAY") or "").strip()
    return gateway not in ("0", "false", "False", "no", "NO", "")


def stamp_pending_and_ensure_index(
    repo_root: str,
    *,
    log_prefix: str | None = None,
    mint_ids: bool = True,
) -> bool:
    """Fill-stamp missing ``#N`` ids, then rebuild the compiled index.

    After a stamp rewrite, always rebuild — do not trust mtime comparison.
    Boot can compile the unstamped tree in the same second the stamper
    writes, which would skip ``ensure_compiled_index`` and leave ``changes``
    serving ``id: 0`` until the next origin advance.

    ``mint_ids=False`` rebuilds the compiled feed only (``Game()`` smokes).
    """
    rewritten = 0
    if mint_ids:
        try:
            from engine import changelog_ids

            _planned, rewritten = changelog_ids.assign_all(
                repo_root, dry_run=False,
            )
        except Exception as exc:
            if log_prefix:
                print(f"{log_prefix} changelog id stamp skipped: {exc}", flush=True)
            rewritten = 0
        else:
            if rewritten and log_prefix:
                print(
                    f"{log_prefix} stamped {rewritten} changelog id(s)",
                    flush=True,
                )
    if rewritten:
        count = rebuild_index_from_sources(repo_root)
        if log_prefix:
            print(
                f"{log_prefix} changelog index rebuilt ({count} entries)",
                flush=True,
            )
        return True
    return ensure_compiled_index(repo_root, log_prefix=log_prefix)


def ensure_compiled_index(repo_root: str, *, log_prefix: str | None = None) -> bool:
    """Rebuild the compiled index when sources are ahead of it.

    Called on game boot and after auto-deploy sync/overlay so ``changes`` never
    depends on agents remembering ``tools/build_changelog_index.py`` or paid
    GitHub Actions gates. Uses full mtime + count checks (not per ``changes``
    tap — that path stays count-only via ``index_likely_stale`` unless
    headlines are still missing ``#N``).
    """
    if not index_needs_rebuild(repo_root):
        return False
    count = rebuild_index_from_sources(repo_root)
    if log_prefix:
        print(
            f"{log_prefix} changelog index rebuilt ({count} entries)",
            flush=True,
        )
    return True


def _enrich_index_entries(entries):
    """Precompute audience/staff flags at build time (runtime filter is O(n))."""
    from engine.changelog_audience import (
        entry_audience,
        entry_is_staff_only,
        strip_leading_tags,
    )

    for entry in entries:
        tags, _ = strip_leading_tags(entry.get("summary") or "")
        entry["audience"] = sorted(entry_audience(tags))
        entry["staff_only"] = bool(entry_is_staff_only(entry))
    return entries


def _slim_index_entry(entry: dict) -> dict:
    """Drop ``full`` prose for fragment slugs; keep monolith rows intact."""
    slim = {k: entry[k] for k in _INDEX_LIST_KEYS if k in entry}
    slug = (slim.get("slug") or "").strip()
    if not slug:
        full = entry.get("full")
        if full:
            slim["full"] = full
    return slim


def write_index(repo_root: str, sig: tuple, entries: list) -> str:
    """Write the compiled index; return the path written."""
    entries = [
        _slim_index_entry(row) for row in _enrich_index_entries(list(entries))
    ]
    path = index_path(repo_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "entry_count": len(entries),
        "source_signature": signature_to_json(repo_root, sig),
        "entries": entries,
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == blob:
                    return path
        except OSError:
            pass
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(blob)
    return path
