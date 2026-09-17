"""discord_patch_notes.py -- short player changelog summaries to Discord #news.

Unposted **player-visible** ledger rows become **one** Discord message on
``#news``, bullets joined with newlines, oldest lookup number first so the
channel reads ``#N`` then ``#(N+1)`` on the next line (not a new post).
Staff-only ``[ops]`` / heuristic bullets are skipped. Unset webhook/channel
= silent no-op.

Configure with ``DISCORD_BRIDGE_WEBHOOK_PATCH_NOTES`` or
``DISCORD_BRIDGE_CHANNELS=...,patch_notes:NEWS_CHANNEL_ID``.

Pending work is ``WHERE sequence='player' AND posted=0`` in
``engine/changelog_ledger.py`` — no git diffing, no anchor SHA.
"""

from __future__ import annotations

from engine import changelog_audience
from engine import changelog_ledger
from engine import discord_bridge

_BULLET_MAX = 180
# Several ships that land together share one #news post (newlines, not
# new messages). Sequential #N order is the list order inside that post.
_MAX_BULLETS = 12
_MAX_MESSAGE = 1900


def _repo_root_from_here() -> str:
    import os

    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _entries_for_unposted_slugs(root, slugs: set[str]) -> list[dict]:
    """Load full prose/audience rows (from markdown) for these ledger slugs."""
    if not slugs:
        return []
    from engine.verbs.basic import _load_unreleased_entries

    entries = _load_unreleased_entries(root)
    want = {changelog_ledger.canonical_fragment_slug(s) for s in slugs}
    matched = [
        row for row in entries
        if changelog_ledger.canonical_fragment_slug(row.get("slug") or "") in want
    ]
    # Oldest lookup number first — Discord list order stays sequential.
    matched.sort(
        key=lambda row: (
            int(row.get("id") or 0),
            row.get("sort_ts") or "",
            row.get("slug") or "",
        ),
    )
    return matched


def _short_player_summary(entry: dict) -> str:
    """One bullet line for Discord (tags stripped, length capped)."""
    text = changelog_audience.display_summary(entry.get("summary") or "")
    text = " ".join(text.split())
    if len(text) > _BULLET_MAX:
        text = text[: _BULLET_MAX - 3] + "..."
    return text


def player_visible_entries(entries: list[dict]) -> list[dict]:
    """Drop staff-only bullets; keep entries a SUPERS player would see in ``changes``."""
    out = []
    for entry in entries:
        if changelog_audience.entry_is_staff_only(entry):
            continue
        if not changelog_audience.visible_to_viewer(entry, "supers", is_gm=False):
            continue
        out.append(entry)
    return out


def format_patch_notes_message(entries: list[dict]) -> str:
    """Build one Discord body: header, sequential bullets, one footer."""
    lines = ["**Patch notes** — what landed in-game:"]
    shown = entries[:_MAX_BULLETS]
    ids = []
    for entry in shown:
        summary = _short_player_summary(entry)
        if not summary:
            continue
        cid = int(entry.get("id") or 0)
        if cid:
            ids.append(cid)
            lines.append(f"• #{cid} {summary} — type `changes {cid}` in-game")
        else:
            lines.append(f"• {summary}")
    extra = len(entries) - len(shown)
    if extra > 0:
        lines.append(f"• …and {extra} more — type `changes` in-game for the full list.")
    lines.append("")
    if len(ids) == 1:
        cid = ids[0]
        lines.append(
            f"In-game: type `changes {cid}` for #{cid}, "
            "or `changes` for the recent list."
        )
    elif ids:
        lines.append(
            "In-game: type `changes` for the recent list, "
            "or `changes N` for a numbered ship."
        )
    else:
        lines.append("Full detail: connect and type `changes`.")
    body = "\n".join(lines)
    if len(body) > _MAX_MESSAGE:
        body = body[: _MAX_MESSAGE - 3] + "..."
    return body


def schedule_patch_notes(entries: list[dict]) -> bool:
    """Queue one #news post; return True when scheduled.

    Never post a batch with no ``#N`` — every row here comes straight from
    the ledger, so this should never actually trip, but it stays as a
    defensive guard against a malformed/legacy row.
    """
    numbered = [row for row in entries if int(row.get("id") or 0)]
    if not numbered:
        return False
    body = format_patch_notes_message(numbered)
    return discord_bridge.schedule_discord("patch_notes", body, kind=None)


def pending_player_entries(root) -> list[dict]:
    """Ledger rows not yet posted, enriched with prose/audience for display.

    Returned oldest-id-first so the combined Discord list reads ``#N`` then
    ``#(N+1)`` on the next line.
    """
    unposted = changelog_ledger.unposted_player_entries(root)
    if not unposted:
        return []
    id_by_slug = {
        changelog_ledger.canonical_fragment_slug(row["slug"]): int(row["id"])
        for row in unposted
        if row.get("slug")
    }
    slugs = set(id_by_slug)
    entries = _entries_for_unposted_slugs(root, slugs)
    for entry in entries:
        slug = changelog_ledger.canonical_fragment_slug(entry.get("slug") or "")
        if slug:
            entry["slug"] = slug
        if slug in id_by_slug:
            entry["id"] = id_by_slug[slug]
    visible = player_visible_entries(entries)
    visible.sort(
        key=lambda row: (int(row.get("id") or 0), row.get("slug") or ""),
    )
    return visible


def _next_fitting_chunk(entries: list[dict]) -> list[dict]:
    """Take as many sequential bullets as fit in one Discord message."""
    if not entries:
        return []
    chunk = entries[:_MAX_BULLETS]
    while len(chunk) > 1 and len(format_patch_notes_message(chunk)) > _MAX_MESSAGE:
        chunk = chunk[:-1]
    return chunk


def schedule_for_deploy(root, from_sha: str | None = None, to_sha: str | None = None) -> bool:
    """Post unposted player rows as one #news message (newlines, oldest id first).

    Several ships that land together share a single Discord post. Only split
    into a second message when the batch would exceed Discord's size cap.
    ``from_sha`` / ``to_sha`` are accepted (and ignored) so every existing
    call site — auto-deploy after sync, idle polls, copyover retry — keeps
    working unchanged; posting is driven by the ledger's ``posted`` column.
    """
    del from_sha, to_sha  # kept for call-site compatibility; ledger-driven now
    # pending_player_entries parses every unposted CHANGELOG.d fragment
    # (~1.5s at today's fragment count) and this runs on the boot /
    # copyover critical path. With no Discord route configured that scan
    # could only ever end in "post skipped", so check the route first.
    if not discord_bridge.can_deliver("patch_notes"):
        print(
            "[discord_patch_notes] post skipped (no webhook/channel configured)",
            flush=True,
        )
        return False
    ready = pending_player_entries(root)
    if not ready:
        return False

    posted_slugs = []
    n_posts = 0
    remaining = list(ready)
    while remaining:
        chunk = _next_fitting_chunk(remaining)
        if not chunk:
            break
        if not schedule_patch_notes(chunk):
            break
        n_posts += 1
        for entry in chunk:
            slug = changelog_ledger.canonical_fragment_slug(entry.get("slug") or "")
            if slug:
                posted_slugs.append(slug)
        remaining = remaining[len(chunk) :]
    if posted_slugs:
        changelog_ledger.mark_posted(root, posted_slugs)
        print(
            f"[discord_patch_notes] scheduled {len(posted_slugs)} player "
            f"bullet(s) from ledger as {n_posts} Discord message(s)",
            flush=True,
        )
    else:
        print(
            f"[discord_patch_notes] post skipped (webhook/channel?) — "
            f"{len(ready)} pending bullet(s)",
            flush=True,
        )
    return bool(posted_slugs)


def retry_deferred_patch_notes(root=None) -> bool:
    """Re-scan the ledger for unposted rows (boot / copyover companion)."""
    root_path = root if root is not None else _repo_root_from_here()
    return schedule_for_deploy(root_path)


def catch_up_patch_notes(root, from_sha: str | None = None, to_sha: str | None = None) -> bool:
    """Manual/backfill entry: same as the deploy hook (tools / one-shot live repair)."""
    return schedule_for_deploy(root, from_sha, to_sha)
