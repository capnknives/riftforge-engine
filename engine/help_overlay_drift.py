"""Compare live hedit SQLite overlays to shipped git help canon.

Used by the GM ``hnews`` verb and offline audits. Pure engine -- no game
imports.
"""
from __future__ import annotations

from engine import help_db


def collect_hedit_git_drift(conn, static_topics, *, is_gm: bool = True) -> list[dict]:
    """Return overlay rows whose static canon body differs from hedit.

    Each dict: keyword, author, git_words, overlay_words, word_delta,
    stale_stub, runtime_winner (``git`` when stale stub else ``hedit``).
    Sorted by largest absolute word delta first, then keyword.
    """
    if conn is None:
        return []

    rows: list[dict] = []
    columns_sql = ", ".join(help_db._COLUMNS)
    for row in conn.execute(
        f"SELECT {columns_sql} FROM helpfiles ORDER BY primary_keyword"
    ):
        entry = help_db._row_to_dict(row)
        if entry["gm_only"] and not is_gm:
            continue
        key = str(entry["primary_keyword"] or "").strip().lower()
        if not key:
            continue
        static = static_topics.get(key)
        if not isinstance(static, str) or not static.strip():
            continue
        overlay = entry.get("body_text") or ""
        if overlay.strip() == static.strip():
            continue
        git_words = len(static.split())
        overlay_words = len(overlay.split())
        stale = help_db.overlay_is_stale_stub(overlay, static)
        rows.append(
            {
                "keyword": key,
                "author": entry.get("author") or "?",
                "git_words": git_words,
                "overlay_words": overlay_words,
                "word_delta": git_words - overlay_words,
                "stale_stub": stale,
                "runtime_winner": "git" if stale else "hedit",
            }
        )

    rows.sort(key=lambda r: (-abs(r["word_delta"]), r["keyword"]))
    return rows
