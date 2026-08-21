"""engine/help_sync.py -- push static help text into the help_db overlay.

GM ``hrefresh`` and boot heals use this to **update** hot-edited SQLite rows
from the git-tracked ``HELP_TOPICS`` source instead of deleting overlays so
static pages win. Overlays stay the delivery layer; their body is rewritten
from canon.
"""
from engine import help_db


def push_static_body(
    conn,
    keyword,
    body_text,
    *,
    category="",
    author="hrefresh",
    gm_only=False,
    is_ic=False,
    syntax_block="",
    aliases=(),
):
    """Create or overwrite one help_db row from a static help page body.

    Returns the saved entry dict, or None when ``conn`` is missing.
    """
    if conn is None:
        return None
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return None
    body_text = body_text or ""
    return help_db.save_entry(
        conn,
        keyword=keyword,
        category=category or "",
        body_text=body_text,
        syntax_block=syntax_block or "",
        aliases=list(aliases or ()),
        gm_only=bool(gm_only),
        is_ic=bool(is_ic),
        author=author or "hrefresh",
    )


def push_static_topic(
    conn,
    keyword,
    body_text,
    *,
    category="",
    author="hrefresh",
    gm_only=False,
    is_ic=False,
    syntax_block="",
    aliases=(),
):
    """Same as ``push_static_body`` but returns (updated: bool, message: str)."""
    if conn is None:
        return False, "no database connection"
    entry = push_static_body(
        conn,
        keyword,
        body_text,
        category=category,
        author=author,
        gm_only=gm_only,
        is_ic=is_ic,
        syntax_block=syntax_block,
        aliases=aliases,
    )
    if entry is None:
        return False, "nothing saved"
    return True, f"overlay refreshed for '{entry['primary_keyword']}'"
