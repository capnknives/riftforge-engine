"""discord_staff_squashbug.py -- Discord #staff squashbug commands (sidecar).

GM in-game ``squashbug`` / ``squashbugs`` POST open rows from
``bug_reports.log`` to the Cursor fixer webhook. The staff Discord bot uses
the same engine helper so briefs in ``#staff`` can be queued with
``!squashbug <id>`` without logging into the game.
"""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def report_directory(root=None) -> str:
    """Directory holding ``bug_reports.log`` (bind-mount repo root on live)."""
    base = Path(root) if root is not None else _repo_root()
    return str(base)


def _ensure_fixer_env(root=None) -> None:
    """Load fixer webhook keys from ``.env`` when compose did not inject them."""
    from engine import bug_webhook
    from engine import env_file

    if bug_webhook.webhook_url():
        return
    env_file.apply_repo_env(
        report_directory(root),
        keys=("CURSOR_BUG_WEBHOOK_URL", "CURSOR_BUG_WEBHOOK_AUTH"),
    )


def parse_bug_id(text: str) -> int | None:
    """Parse ``92``, ``#92``, or ``bug 92`` from Discord command args."""
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(None, 1)
    if parts[0].lower() in ("bug", "bugs") and len(parts) == 2:
        raw = parts[1].strip()
    if raw.lower() in ("all",):
        return None
    if raw.startswith("#"):
        raw = raw[1:].strip()
    try:
        return int(raw)
    except ValueError:
        return None


def execute_squashbug(
    bug_id: int,
    *,
    root=None,
    game=None,
) -> tuple[bool, str]:
    """Queue one open bug id for the Cursor fixer webhook."""
    from engine import bug_webhook

    _ensure_fixer_env(root)
    if not bug_webhook.webhook_url():
        return False, "CURSOR_BUG_WEBHOOK_URL is not configured — cannot queue fixer runs."
    directory = str(getattr(game, "report_dir", None) or report_directory(root))
    scheduled, total, scheduled_ids = bug_webhook.schedule_open_bugs(
        directory,
        bug_ids=[bug_id],
        game=game,
    )
    if total == 0:
        return False, (
            f"No open bug report {bug_id} to send "
            "(missing, already resolved, or wrong id)."
        )
    if scheduled == 0:
        return False, f"Bug report {bug_id} matched but webhook queue failed."
    if game and scheduled_ids:
        try:
            from engine import kokid_notify

            kokid_notify.announce_pickup(game, scheduled_ids)
            kokid_notify.poll_after_pickup(game, scheduled_ids)
        except Exception as exc:
            print(f"[discord_staff_squashbug] kokid pickup skipped: {exc}", flush=True)
    return True, f"Queued bug report {bug_id} for the fixer webhook."


def execute_squashbugs_all(*, root=None, game=None) -> tuple[bool, str]:
    """Queue every open bug report for the Cursor fixer webhook."""
    from engine import bug_webhook

    _ensure_fixer_env(root)
    if not bug_webhook.webhook_url():
        return False, "CURSOR_BUG_WEBHOOK_URL is not configured — cannot queue fixer runs."
    directory = str(getattr(game, "report_dir", None) or report_directory(root))
    scheduled, total, scheduled_ids = bug_webhook.schedule_open_bugs(
        directory,
        bug_ids=None,
        game=game,
    )
    if total == 0:
        return False, "No open bugs to send."
    if scheduled == 0:
        return False, f"Matched {total} open bug(s) but webhook queue failed."
    if game and scheduled_ids:
        try:
            from engine import kokid_notify

            kokid_notify.announce_pickup(game, scheduled_ids)
            kokid_notify.poll_after_pickup(game, scheduled_ids)
        except Exception as exc:
            print(f"[discord_staff_squashbug] kokid pickup skipped: {exc}", flush=True)
    return True, f"Queued {scheduled}/{total} open bug(s) for the fixer webhook."
