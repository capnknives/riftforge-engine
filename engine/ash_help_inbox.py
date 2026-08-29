"""ash_help_inbox.py -- questions-net jobs for the Ash helper sidecar.

Game writes ``.ash_help_jobs/*.json`` (no networking). The sidecar answers
and drops ``.ash_help_outbox/*.json``. A tick handler broadcasts those as
Ash [Helper AI] on the questions channel.

Stdlib only. Sidecar owns Groq/Gemini HTTP.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

JOBS_DIR_NAME = ".ash_help_jobs"
OUTBOX_DIR_NAME = ".ash_help_outbox"
ASH_FACE = "Ash [Helper AI]"
ASH_SPEAKER_KEY = "ash-help"
RATE_SECONDS = 20
JOB_MAX_AGE_S = 600
QUESTION_MAX = 400


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def jobs_dir(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / JOBS_DIR_NAME


def outbox_dir(root=None) -> Path:
    base = Path(root) if root is not None else _repo_root()
    return base / OUTBOX_DIR_NAME


def ensure_jobs_dir(root=None) -> Path:
    path = jobs_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_outbox_dir(root=None) -> Path:
    path = outbox_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _account_token(game, speaker) -> str:
    try:
        from engine import accounts as accounts_mod

        account = accounts_mod.account_for_character(game, speaker)
        if account is not None:
            return str(account.name or account.display_name or "")
    except Exception:
        pass
    return str(getattr(speaker, "key", "") or "")


def _rate_ok(game, token: str) -> bool:
    if not token:
        return True
    stamp = getattr(game, "_ash_help_last", None)
    if not isinstance(stamp, dict):
        stamp = {}
        game._ash_help_last = stamp
    now = time.time()
    last = float(stamp.get(token) or 0)
    if now - last < RATE_SECONDS:
        return False
    stamp[token] = now
    return True


def enqueue_question(game, speaker, question: str, *, root=None) -> Path | None:
    """Drop one sidecar job. None when rate-limited or empty."""
    text = str(question or "").strip()
    if len(text) < 4:
        return None
    token = _account_token(game, speaker)
    if not _rate_ok(game, token):
        return None
    directory = ensure_jobs_dir(root)
    payload = {
        "account": token,
        "character_key": str(getattr(speaker, "key", "") or ""),
        "face": str(getattr(speaker, "key", "") or token),
        "question": text[:QUESTION_MAX],
        "ts": time.time(),
    }
    name = f"job-{int(time.time() * 1000)}-{os.getpid()}.json"
    path = directory / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_outbox_reply(reply: str, *, root=None, job_id: str = "") -> Path | None:
    """Sidecar: queue one Ash line for the game tick."""
    text = str(reply or "").strip()
    if not text:
        return None
    directory = ensure_outbox_dir(root)
    payload = {
        "face": ASH_FACE,
        "speaker": ASH_SPEAKER_KEY,
        "message": text[:QUESTION_MAX],
        "ts": time.time(),
        "job_id": str(job_id or ""),
    }
    name = f"msg-{int(time.time() * 1000)}-{os.getpid()}.json"
    path = directory / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _load_payload(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def prune_stale_jobs(root=None, *, max_age_s: int = JOB_MAX_AGE_S) -> int:
    directory = jobs_dir(root)
    if not directory.is_dir():
        return 0
    cutoff = time.time() - max_age_s
    n = 0
    for path in directory.glob("job-*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                n += 1
        except OSError:
            pass
    return n


def tick_ash_help_outbox(game) -> None:
    """Broadcast pending Ash replies; drop stale unanswered jobs."""
    from engine import questions_channel

    prune_stale_jobs()
    directory = outbox_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("msg-*.json")):
        payload = _load_payload(path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if not payload:
            continue
        message = str(payload.get("message") or "").strip()
        if not message:
            continue
        questions_channel.broadcast_questions(
            game,
            None,
            message,
            face=ASH_FACE,
            speaker_key=ASH_SPEAKER_KEY,
            enqueue_ash=False,
        )


def register_tick(game) -> None:
    from engine.tick_registry import register_tick

    register_tick(
        game,
        tick_ash_help_outbox,
        order=6,
        name="ash_help_outbox",
    )
