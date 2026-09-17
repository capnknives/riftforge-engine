"""trivia_session.py -- on-demand trivia round state on game.meta."""

from __future__ import annotations

from engine import trivia_corpus

SESSION_META_KEY = "trivia_session"
IDLE_WALL_SECONDS = 180.0
# Bug report 1528: lifetime trivia still accrues, but only 50 per game-day.
DAILY_POINT_CAP = 50


def _game_day_index(game) -> int:
    """Compressed game-day bucket for the daily trivia cap."""
    from engine.game_clock import TICKS_PER_GAME_DAY

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    return ticks // max(1, int(TICKS_PER_GAME_DAY))


def sync_daily_trivia_bucket(account, game) -> int:
    """Reset today's bucket on a new game-day. Returns today's count.

    Day 0 is a real game-day -- do not treat it as unset (``0 or -1``).
    """
    day = _game_day_index(game)
    try:
        stored = getattr(account, "trivia_points_day_index", None)
        stored_day = int(stored) if stored is not None else -1
    except (TypeError, ValueError):
        stored_day = -1
    if stored_day != day:
        account.trivia_points_today = 0
        account.trivia_points_day_index = day
    return int(getattr(account, "trivia_points_today", 0) or 0)


def _credit_trivia_points(account, game, points: int) -> tuple[int, str]:
    """Credit *points* up to the daily cap. Returns (awarded, extra_msg)."""
    earned = sync_daily_trivia_bucket(account, game)
    if earned >= DAILY_POINT_CAP:
        return 0, (
            f"You've hit today's trivia cap ({DAILY_POINT_CAP} points). "
            "Someone else can still answer."
        )
    award = min(int(points), DAILY_POINT_CAP - earned)
    account.trivia_points_today = earned + award
    account.trivia_points = int(getattr(account, "trivia_points", 0) or 0) + award
    extra = ""
    if award < points:
        extra = f" Daily cap {DAILY_POINT_CAP}; credited {award}."
    return award, extra


def _session(game) -> dict:
    meta = getattr(game, "meta", None)
    if not isinstance(meta, dict):
        meta = {}
        game.meta = meta
    raw = meta.get(SESSION_META_KEY)
    if not isinstance(raw, dict):
        raw = {}
        meta[SESSION_META_KEY] = raw
    return raw


def is_active(game) -> bool:
    return bool(_session(game).get("active"))


def clear_session(game) -> None:
    meta = getattr(game, "meta", None)
    if isinstance(meta, dict):
        meta.pop(SESSION_META_KEY, None)


def _idle_ticks(game) -> int:
    from engine import game_clock_tuning as clock_mod

    return max(1, clock_mod.ticks_for_wall_seconds(IDLE_WALL_SECONDS, game))


def _guesser_key(game, character) -> str:
    """Stable id for one guess per account (or character) per live question."""
    from engine import accounts as accounts_mod

    account = accounts_mod.account_for_character(game, character)
    if account is not None:
        return f"a:{account.name}"
    key = getattr(character, "key", None)
    return f"c:{key}" if key else ""


def _missed_guessers(sess: dict) -> list[str]:
    raw = sess.get("missed_guessers")
    if not isinstance(raw, list):
        raw = []
        sess["missed_guessers"] = raw
    return raw


def start_round(game, character, pool: str) -> tuple[bool, str]:
    """Begin one on-demand question for subscribers on *pool*."""
    if is_active(game):
        return False, "A trivia question is already live. Answer it or wait for timeout."
    pool_key = (pool or "general").strip().lower()
    if pool_key not in ("general", "spn"):
        return False, "Usage: trivia start | trivia start spn"
    question = trivia_corpus.pick_question(pool_key)
    if question is None:
        return False, f"No questions loaded for pool '{pool_key}'."
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    sess = _session(game)
    sess.clear()
    sess.update(
        {
            "active": True,
            "pool": pool_key,
            "question": question,
            "started_tick": now,
            "expires_tick": now + _idle_ticks(game),
            "starter_key": getattr(character, "key", None),
            "answered_account": "",
            "answered_points": 0,
            "missed_guessers": [],
        }
    )
    return True, ""


def current_question(game) -> dict | None:
    if not is_active(game):
        return None
    q = _session(game).get("question")
    return q if isinstance(q, dict) else None


def try_answer(game, character, guess: str) -> tuple[bool, str, int, str]:
    """Score one guess. Returns (correct, message, points_awarded, answer_text)."""
    if not is_active(game):
        return False, "No trivia question is live. Type trivia start or trivia start spn.", 0, ""
    sess = _session(game)
    if sess.get("answered_account"):
        return False, "Someone already answered this question.", 0, ""
    question = current_question(game)
    if question is None:
        clear_session(game)
        return False, "Trivia state was corrupt — try trivia start again.", 0, ""
    answer_text = str(question.get("answer") or "")
    gkey = _guesser_key(game, character)
    missed = _missed_guessers(sess)
    if gkey and gkey in missed:
        return False, "You already missed this question.", 0, ""
    if not trivia_corpus.guess_matches(guess, question):
        if gkey and gkey not in missed:
            missed.append(gkey)
        return False, "Not quite.", 0, ""
    from engine import accounts as accounts_mod

    account = accounts_mod.account_for_character(game, character)
    if account is None:
        return False, "Link an account first (help account) to earn trivia points.", 0, ""
    points = int(question.get("points") or trivia_corpus.points_for_difficulty(
        question.get("difficulty")
    ))
    points = max(1, min(3, points))
    awarded, cap_msg = _credit_trivia_points(account, game, points)
    if awarded <= 0:
        # Do not consume first-wins — another account may still be under cap.
        return False, cap_msg, 0, ""
    accounts_mod._try_mark_account_dirty(game, account, label="trivia_points")
    sess["answered_account"] = account.name
    sess["answered_points"] = awarded
    clear_session(game)
    return True, (
        f"Correct! +{awarded} trivia point{'s' if awarded != 1 else ''} "
        f"({account.trivia_points} trivia lifetime).{cap_msg}"
    ), awarded, answer_text


def tick_idle_timeout(game) -> tuple[bool, str]:
    """End the live question when idle timer elapses.

    Returns (timed_out, answer_text) so callers can reveal the solution.
    """
    if not is_active(game):
        return False, ""
    sess = _session(game)
    if sess.get("answered_account"):
        clear_session(game)
        return False, ""
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    expires = int(sess.get("expires_tick") or 0)
    if expires and now < expires:
        return False, ""
    question = current_question(game) or {}
    answer_text = str(question.get("answer") or "")
    clear_session(game)
    return True, answer_text


def format_question_prompt(question: dict) -> str:
    """Player-facing question block with lettered choices."""
    lines = [str(question.get("question") or "").strip()]
    pts = int(question.get("points") or 2)
    diff = str(question.get("difficulty") or "medium")
    lines.append(f"[{diff} — worth {pts} point{'s' if pts != 1 else ''}]")
    for idx, choice in enumerate(question.get("choices") or []):
        letter = chr(ord("A") + idx)
        lines.append(f"  {letter}) {choice}")
    lines.append("Type trivia <answer>, trivia answer <A-D>, or trivia <A-D>.")
    return "\r\n".join(lines)
