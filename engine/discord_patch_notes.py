"""discord_patch_notes.py -- short player changelog summaries to Discord #patch-notes.

After auto-deploy syncs ``origin/main``, scan CHANGELOG.d fragments touched in
the deploy range, pick **player-visible** bullets (same filter as in-game
``changes`` for non-GMs), and post a brief list via ``discord_bridge`` tag
``patch_notes``.

Configure with ``DISCORD_BRIDGE_WEBHOOK_PATCH_NOTES`` or
``DISCORD_BRIDGE_CHANNELS=...,patch_notes:CHANNEL_ID``.

Ops-only ``[ops]`` / heuristic staff bullets are skipped — public patch channel
stays player-facing. Unset webhook/channel = silent no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from engine import changelog_audience
from engine import discord_bridge

_STATE_NAME = ".discord_patch_notes_state.json"
_BULLET_MAX = 180
_MAX_BULLETS = 12
_MAX_MESSAGE = 1900


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def state_path(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / _STATE_NAME


def _load_state(root) -> dict:
    path = state_path(root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(root, payload: dict) -> None:
    path = state_path(root)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git(args, *, cwd):
    out = subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return out.strip()


def changelog_slugs_in_deploy(root, from_sha: str, to_sha: str) -> list[str]:
    """Return ``CHANGELOG.d`` fragment slugs touched between two SHAs."""
    root_s = str(root)
    paths: list[str] = []
    from_sha = (from_sha or "").strip()
    to_sha = (to_sha or "").strip()
    if not to_sha:
        return []
    try:
        if from_sha and from_sha != to_sha:
            from tools.apply_pr_fix import changed_files_between

            paths = changed_files_between(from_sha, to_sha, cwd=root_s)
        else:
            from tools.apply_pr_fix import files_in_commit

            paths = files_in_commit(to_sha, cwd=root_s)
    except (subprocess.CalledProcessError, OSError):
        return []

    slugs: list[str] = []
    prefix = "CHANGELOG.d/"
    for rel in paths:
        rel = rel.replace("\\", "/")
        if not rel.startswith(prefix) or not rel.endswith(".md"):
            continue
        name = rel[len(prefix) :]
        if name.lower() == "readme.md":
            continue
        slug = name[:-3]
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _entries_for_slugs(root, slugs: list[str]) -> list[dict]:
    """Load index rows matching fragment slugs (newest first)."""
    if not slugs:
        return []
    from engine import changelog_index as changelog_index_mod

    # Stamp then rebuild — ``ensure_compiled_index`` can keep a same-second
    # compiled feed with ``id: 0`` after the game host just minted ``#N``.
    stamp = getattr(changelog_index_mod, "stamp_pending_and_ensure_index", None)
    if callable(stamp):
        mint = False
        should = getattr(changelog_index_mod, "should_mint_ids_on_boot", None)
        if callable(should):
            mint = bool(should())
        stamp(str(root), mint_ids=mint)
    else:
        changelog_index_mod.ensure_compiled_index(str(root))
    entries = changelog_index_mod.load_index_file(
        changelog_index_mod.index_path(str(root))
    )
    if not entries:
        return []
    want = set(slugs)
    matched = [row for row in entries if (row.get("slug") or "") in want]
    matched.sort(
        key=lambda row: (
            row.get("sort_ts") or "",
            row.get("slug") or "",
        ),
        reverse=True,
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
    """Build the Discord body for one deploy batch."""
    lines = ["**Patch notes** — what landed in-game:"]
    shown = entries[:_MAX_BULLETS]
    first_id = 0
    for entry in shown:
        summary = _short_player_summary(entry)
        if not summary:
            continue
        cid = int(entry.get("id") or 0)
        if cid:
            if not first_id:
                first_id = cid
            lines.append(f"• #{cid} {summary} — type `changes {cid}` in-game")
        else:
            lines.append(f"• {summary}")
    extra = len(entries) - len(shown)
    if extra > 0:
        lines.append(f"• …and {extra} more — type `changes` in-game for the full list.")
    lines.append("")
    if first_id:
        lines.append(
            f"In-game: type `changes {first_id}` for #{first_id}, "
            "or `changes` for the recent list."
        )
    else:
        lines.append("Full detail: connect and type `changes`.")
    body = "\n".join(lines)
    if len(body) > _MAX_MESSAGE:
        body = body[: _MAX_MESSAGE - 3] + "..."
    return body


def schedule_patch_notes(entries: list[dict]) -> bool:
    """Queue one #patch-notes post; return True when scheduled.

    Never post a batch with no ``#N`` — that is the Discord footer
    ``Full detail: connect and type changes`` with no lookup number.
    """
    numbered = [row for row in entries if int(row.get("id") or 0)]
    if not numbered:
        return False
    body = format_patch_notes_message(numbered)
    return discord_bridge.schedule_discord("patch_notes", body, kind=None)


def _posted_slugs(state: dict) -> set[str]:
    raw = state.get("posted_slugs") or []
    return {str(item).strip() for item in raw if str(item).strip()}


def _bootstrap_anchor_sha(root, from_sha: str, to_sha: str, *, max_commits: int = 40) -> str:
    """When nothing posted yet, scan recent history so late webhook ships catch up."""
    from_s = (from_sha or "").strip()
    to_s = (to_sha or "").strip()
    if not to_s:
        return from_s
    if max_commits <= 0:
        return from_s
    try:
        wide = _git(["rev-parse", f"{to_s}~{max_commits}"], cwd=str(root))
    except (subprocess.CalledProcessError, OSError):
        return from_s
    wide = (wide or "").strip()
    if not wide:
        return from_s
    if from_s and from_s != to_s:
        # Keep whichever is earlier in history (more backfill).
        try:
            merge = _git(["merge-base", from_s, wide], cwd=str(root))
            return (merge or wide).strip() or from_s
        except (subprocess.CalledProcessError, OSError):
            return wide
    return wide


def _scan_anchor_sha(state: dict, from_sha: str, to_sha: str, *, root=None) -> str:
    """Start SHA for cumulative slug scan (backfills missed single-hop deploys)."""
    anchor = (state.get("patch_notes_anchor_sha") or "").strip()
    from_s = (from_sha or "").strip()
    to_s = (to_sha or "").strip()
    if not to_s:
        return from_s or anchor
    if anchor and anchor != to_s:
        return anchor
    if not _posted_slugs(state) and not anchor and root is not None:
        return _bootstrap_anchor_sha(root, from_s, to_s)
    return from_s or anchor


def pending_player_entries(
    root,
    from_sha: str,
    to_sha: str,
    posted_slugs: set[str],
) -> tuple[list[dict], list[str]]:
    """Player-visible index rows in *from_sha*..*to_sha* not yet posted."""
    slugs = changelog_slugs_in_deploy(root, from_sha, to_sha)
    if not slugs:
        return [], slugs
    entries = _entries_for_slugs(root, slugs)
    visible = player_visible_entries(entries)
    new_entries = [
        row for row in visible if (row.get("slug") or "") not in posted_slugs
    ]
    return new_entries, slugs


def schedule_for_deploy(root, from_sha: str, to_sha: str) -> bool:
    """Post summaries for CHANGELOG.d fragments once they have in-game ``#N``.

    Same tip SHA may be scanned again: idle polls and copyover mint ids
    after the first pass. Do not treat ``last_processed_sha`` as done
    while unnumbered player bullets are still unposted.
    """
    root_path = Path(root)
    to_sha = (to_sha or "").strip()
    if not to_sha:
        return False

    state = _load_state(root_path)
    already = state.get("last_processed_sha") == to_sha
    posted = _posted_slugs(state)
    anchor = _scan_anchor_sha(state, from_sha, to_sha, root=root_path)
    new_entries, slugs = pending_player_entries(
        root_path,
        anchor,
        to_sha,
        posted,
    )

    ready = [row for row in new_entries if int(row.get("id") or 0)]
    waiting = [row for row in new_entries if not int(row.get("id") or 0)]
    if waiting:
        # Hold the whole batch until every player bullet has #N. Posting the
        # numbered subset first taught Discord "type changes" with no number,
        # then last_processed_sha blocked the retry after stamp.
        if not already:
            print(
                f"[discord_patch_notes] waiting for changelog id stamp on "
                f"{len(waiting)} bullet(s) — will retry after copyover "
                f"or the next idle poll",
                flush=True,
            )
            state["last_processed_sha"] = to_sha
            state["posted_slugs"] = sorted(posted)
            _save_state(root_path, state)
        return False

    if already and not ready:
        return False

    scheduled = False
    if ready:
        scheduled = schedule_patch_notes(ready)

    state["last_processed_sha"] = to_sha

    if scheduled:
        for row in ready:
            slug = (row.get("slug") or "").strip()
            if slug:
                posted.add(slug)
        state["posted_slugs"] = sorted(posted)
        state["patch_notes_anchor_sha"] = to_sha
        state["last_sha"] = to_sha
        state["last_from_sha"] = anchor
        state["slugs"] = slugs
        print(
            f"[discord_patch_notes] scheduled {len(ready)} player bullet(s) "
            f"anchor={anchor[:12]} tip={to_sha[:12]}",
            flush=True,
        )
    elif not new_entries:
        state["posted_slugs"] = sorted(posted)
        state["patch_notes_anchor_sha"] = to_sha
        print(
            f"[discord_patch_notes] nothing player-visible to post for "
            f"{to_sha[:12]} (anchor={anchor[:12]})",
            flush=True,
        )
    else:
        print(
            f"[discord_patch_notes] post skipped (webhook/channel?) — "
            f"{len(ready)} pending bullet(s) anchor={anchor[:12]}",
            flush=True,
        )

    _save_state(root_path, state)
    return scheduled


def retry_deferred_patch_notes(root=None) -> bool:
    """Re-scan after copyover/idle stamp so Discord can post numbered bullets."""
    root_path = Path(root) if root is not None else _repo_root()
    state = _load_state(root_path)
    to_sha = (state.get("last_processed_sha") or state.get("last_sha") or "").strip()
    if not to_sha:
        try:
            to_sha = _git(["rev-parse", "HEAD"], cwd=str(root_path))
        except (subprocess.CalledProcessError, OSError):
            return False
        to_sha = (to_sha or "").strip()
    if not to_sha:
        return False
    from_sha = (state.get("patch_notes_anchor_sha") or "").strip()
    return schedule_for_deploy(root_path, from_sha, to_sha)


def catch_up_patch_notes(root, from_sha: str, to_sha: str) -> bool:
    """Manual/backfill entry: same as deploy hook (tools / one-shot live repair)."""
    return schedule_for_deploy(root, from_sha, to_sha)
