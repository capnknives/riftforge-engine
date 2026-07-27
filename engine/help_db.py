"""engine/help_db.py -- SQLite-backed helpfile overlay: CRUD + 3-layer search.

This is the "hot-editable" layer over the static ``HELP_TOPICS`` dict in
root ``help_topics.py`` (docs/plans/helpfile_editing_system.md). A GM can
add or fix a help page in-game with the ``hedit`` command (see
``engine/verbs/basic.py`` / ``engine/connection.py``'s ``help_edit`` state)
without a deploy; the static file stays the PR-reviewed source of truth for
everything else.

Every function here takes ``conn`` -- the SAME sqlite3 connection
``engine/persistence.py`` already opens as ``Game.db`` (see that module's
``_SCHEMA`` for the ``helpfiles`` / ``help_aliases`` / ``help_fts`` tables
and their sync triggers). No networking, no world model, no SUPERS import --
same purity discipline as ``engine/persistence.py``.
"""
import time


# Column order used everywhere a helpfiles row becomes a plain dict, so a
# caller never has to remember sqlite3's positional tuple order.
_COLUMNS = (
    "primary_keyword", "category", "gm_only", "is_ic",
    "syntax_block", "body_text", "author", "last_modified",
)


def _row_to_dict(row):
    """Turn one sqlite3 result tuple (in ``_COLUMNS`` order) into a dict."""
    return dict(zip(_COLUMNS, row))


def save_entry(
    conn, *, keyword, category, body_text, syntax_block, aliases,
    gm_only, is_ic, author,
):
    """Create or overwrite one ``helpfiles`` row plus its alias rows.

    ``help_fts`` stays in sync automatically -- the triggers in
    ``persistence.py`` mirror every INSERT/UPDATE/DELETE on ``helpfiles``.
    Returns the freshly saved entry (via ``get_entry``) so a caller (the
    ``hedit`` ``/save`` handler) can echo back exactly what landed.
    """
    keyword = keyword.strip().lower()
    now = time.time()
    # ON CONFLICT upsert -- primary_keyword is the natural primary key, so a
    # second save of the same keyword updates in place (bumping
    # last_modified) instead of erroring.
    conn.execute(
        """
        INSERT INTO helpfiles
            (primary_keyword, category, gm_only, is_ic, syntax_block,
             body_text, author, last_modified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(primary_keyword) DO UPDATE SET
            category=excluded.category,
            gm_only=excluded.gm_only,
            is_ic=excluded.is_ic,
            syntax_block=excluded.syntax_block,
            body_text=excluded.body_text,
            author=excluded.author,
            last_modified=excluded.last_modified
        """,
        (
            keyword, category, int(bool(gm_only)), int(bool(is_ic)),
            syntax_block, body_text, author, now,
        ),
    )
    # Replace the alias set wholesale -- simpler and safer than diffing an
    # old list against a new one for what is, in practice, a handful of rows.
    conn.execute("DELETE FROM help_aliases WHERE primary_keyword = ?", (keyword,))
    seen = set()
    for alias in aliases or ():
        alias = alias.strip().lower()
        if not alias or alias == keyword or alias in seen:
            continue
        seen.add(alias)
        conn.execute(
            "INSERT OR REPLACE INTO help_aliases (alias, primary_keyword) "
            "VALUES (?, ?)",
            (alias, keyword),
        )
    conn.commit()
    return get_entry(conn, keyword)


def delete_entry(conn, keyword):
    """Remove a DB-overlay entry (and its aliases). Leaves any static
    ``HELP_TOPICS`` page with the same keyword untouched -- it simply stops
    being shadowed.
    """
    keyword = keyword.strip().lower()
    conn.execute("DELETE FROM help_aliases WHERE primary_keyword = ?", (keyword,))
    conn.execute("DELETE FROM helpfiles WHERE primary_keyword = ?", (keyword,))
    conn.commit()


def get_entry(conn, keyword, *, is_gm=True):
    """Layer 1: exact ``primary_keyword`` or alias match (case-insensitive).

    ``is_gm=False`` hides a ``gm_only`` entry from a non-staff lookup (the
    player-facing ``help`` path); the editor and internal saves default to
    ``is_gm=True`` since they're already gated by ``_is_gm`` at the command
    level.
    """
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return None
    columns_sql = ", ".join(_COLUMNS)
    row = conn.execute(
        f"SELECT {columns_sql} FROM helpfiles WHERE primary_keyword = ?",
        (keyword,),
    ).fetchone()
    if row is None:
        alias_row = conn.execute(
            "SELECT primary_keyword FROM help_aliases WHERE alias = ?",
            (keyword,),
        ).fetchone()
        if alias_row is None:
            return None
        row = conn.execute(
            f"SELECT {columns_sql} FROM helpfiles WHERE primary_keyword = ?",
            (alias_row[0],),
        ).fetchone()
        if row is None:
            return None
    entry = _row_to_dict(row)
    if entry["gm_only"] and not is_gm:
        return None
    return entry


def list_aliases(conn, keyword):
    """All aliases pointing at ``keyword``, alphabetical."""
    keyword = (keyword or "").strip().lower()
    rows = conn.execute(
        "SELECT alias FROM help_aliases WHERE primary_keyword = ? ORDER BY alias",
        (keyword,),
    ).fetchall()
    return [r[0] for r in rows]


def _fts_query(query):
    """Turn a free-typed query into a safe FTS5 MATCH expression.

    FTS5's MATCH syntax treats bare quotes/colons/hyphens/parens as query
    operators, so a raw player-typed string can raise "fts5: syntax error".
    Wrapping every whitespace-split token as its own quoted phrase (with
    internal quotes doubled, the FTS5 escape convention) searches for the
    literal text while sidestepping operator parsing entirely; FTS5's
    default token join is AND, so multi-word queries still narrow sensibly.
    """
    tokens = query.split()
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in tokens)


def search_fts(conn, query, *, is_gm=False):
    """Layer 2: FTS5 full-text search over keyword + body, best match first.

    ``bm25()`` returns a MORE NEGATIVE score for a BETTER match, so a plain
    ascending ``ORDER BY`` is already best-first -- do not negate the rank
    (a common misreading of bm25's sign convention would put the *worst*
    match first instead).
    """
    query = (query or "").strip()
    if not query:
        return None
    columns_sql = ", ".join(f"helpfiles.{c}" for c in _COLUMNS)
    try:
        row = conn.execute(
            f"""
            SELECT {columns_sql}
            FROM help_fts
            JOIN helpfiles ON help_fts.rowid = helpfiles.rowid
            WHERE help_fts MATCH ? AND (helpfiles.gm_only = 0 OR ? = 1)
            ORDER BY bm25(help_fts)
            LIMIT 1
            """,
            (_fts_query(query), int(bool(is_gm))),
        ).fetchone()
    except Exception:
        # Malformed/empty FTS5 query (e.g. all-punctuation input) -- treat
        # as "no match" rather than raising into the player's session.
        return None
    return _row_to_dict(row) if row is not None else None


def _levenshtein(a, b):
    """Classic edit-distance DP, O(len(a) * len(b)). Pure stdlib -- no
    trigram extension, no third-party fuzzy-match library.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr
    return prev[-1]


def fuzzy_suggest(conn, query, *, is_gm=False, extra_candidates=None, max_distance=3):
    """Layer 3: closest known keyword/alias by edit distance ("Did you
    mean?"). Returns the single closest candidate string, or None when
    nothing is within ``max_distance``.

    ``extra_candidates`` lets a caller outside ``engine/`` (``cmd_help``,
    which also knows the static ``HELP_TOPICS`` keys and ``COMMANDS``
    verbs) fold those in without this module importing anything beyond the
    DB it already owns.
    """
    query = (query or "").strip().lower()
    if not query:
        return None
    candidates = set()
    for keyword, gm_only in conn.execute(
        "SELECT primary_keyword, gm_only FROM helpfiles"
    ).fetchall():
        if gm_only and not is_gm:
            continue
        candidates.add(keyword)
    for (alias,) in conn.execute("SELECT alias FROM help_aliases").fetchall():
        candidates.add(alias)
    candidates.update(extra_candidates or ())
    candidates.discard(query)

    best = None
    best_distance = max_distance + 1
    for candidate in candidates:
        distance = _levenshtein(query, candidate)
        if distance < best_distance:
            best = candidate
            best_distance = distance
    return best
