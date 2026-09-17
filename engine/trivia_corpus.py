"""trivia_corpus.py -- load offline trivia question pools (stdlib only)."""

from __future__ import annotations

import html
import json
import os
import random
import re

_CORPUS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "supers",
    "content",
    "trivia",
)

_POOL_CACHE: dict[str, list[dict]] = {}
_MANIFEST: dict | None = None

_DIFFICULTY_POINTS = {"easy": 1, "medium": 2, "hard": 3}


def points_for_difficulty(difficulty: str | None) -> int:
    """Easy=1, medium/standard=2, hard=3."""
    key = (difficulty or "medium").strip().lower()
    if key in ("easy", "1"):
        return 1
    if key in ("hard", "3"):
        return 3
    return 2


def _decode_text(text: str) -> str:
    """Strip HTML entities from imported corpora."""
    out = html.unescape(str(text or ""))
    return re.sub(r"\s+", " ", out).strip()


def _normalize_row(row: dict, *, pool: str) -> dict | None:
    question = _decode_text(row.get("question"))
    answer = _decode_text(row.get("answer"))
    if not question or not answer:
        return None
    choices_raw = row.get("choices") or []
    choices = [_decode_text(c) for c in choices_raw if _decode_text(c)]
    if len(choices) < 2:
        choices = ["True", "False"]
    difficulty = (row.get("difficulty") or "medium").strip().lower()
    points = int(row.get("points") or points_for_difficulty(difficulty))
    points = max(1, min(3, points))
    qid = str(row.get("id") or f"{pool}:{question[:40]}")
    return {
        "id": qid,
        "pool": pool,
        "category": _decode_text(row.get("category") or pool.title()),
        "question": question,
        "choices": choices,
        "answer": answer,
        "difficulty": difficulty,
        "points": points,
    }


def load_manifest() -> dict:
    """Return trivia manifest (pools, scoring table, attribution)."""
    global _MANIFEST
    if _MANIFEST is not None:
        return _MANIFEST
    path = os.path.join(_CORPUS_DIR, "manifest.json")
    if not os.path.isfile(path):
        _MANIFEST = {"pools": {}, "scoring": _DIFFICULTY_POINTS}
        return _MANIFEST
    with open(path, encoding="utf-8") as fh:
        _MANIFEST = json.load(fh)
    return _MANIFEST


def load_pool(pool: str) -> list[dict]:
    """Load and cache one pool ('general' or 'spn')."""
    key = (pool or "").strip().lower()
    if key in _POOL_CACHE:
        return _POOL_CACHE[key]
    manifest = load_manifest()
    pool_info = (manifest.get("pools") or {}).get(key) or {}
    filename = pool_info.get("file") or f"{key}.json"
    path = os.path.join(_CORPUS_DIR, filename)
    rows: list[dict] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        for raw in payload.get("questions") or []:
            norm = _normalize_row(raw, pool=key)
            if norm is not None:
                rows.append(norm)
    _POOL_CACHE[key] = rows
    return rows


def pick_question(pool: str, *, exclude_ids: set[str] | None = None) -> dict | None:
    """Return a random question from *pool*, skipping *exclude_ids*."""
    rows = load_pool(pool)
    if not rows:
        return None
    exclude = exclude_ids or set()
    candidates = [r for r in rows if r["id"] not in exclude]
    if not candidates:
        candidates = list(rows)
    if not candidates:
        return None
    row = random.choice(candidates)
    # Shuffle MCQ choices; track answer index after shuffle.
    choices = list(row["choices"])
    random.shuffle(choices)
    answer = row["answer"]
    return {
        **row,
        "choices": choices,
        "answer_index": choices.index(answer) if answer in choices else 0,
    }


def normalize_guess(text: str) -> str:
    """Lowercase alphanumeric guess for fuzzy match."""
    cleaned = _decode_text(text).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return " ".join(cleaned.split())


def guess_matches(guess: str, question: dict) -> bool:
    """True when *guess* matches the correct answer (letter or exact text).

    Bug report 1526: substring matching (``g in a or a in g``) made
    single letters like ``f`` count as correct for ``True`` / ``False``.
    Letter A-D (or 1-4) only map onto the choice list; free text must
    match the whole normalized answer.
    """
    raw = (guess or "").strip()
    if not raw:
        return False
    low = raw.lower()
    # ``trivia answer A`` is the same guess as ``trivia A``.
    if low.startswith("answer "):
        raw = raw[7:].strip()
    choices = question.get("choices") or []
    answer = question.get("answer") or ""
    # Single letter A-D
    if len(raw) == 1 and raw.upper() in "ABCD" and len(choices) >= 2:
        idx = ord(raw.upper()) - ord("A")
        if 0 <= idx < len(choices):
            return normalize_guess(choices[idx]) == normalize_guess(answer)
        return False
    # Numbered 1-4
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            return normalize_guess(choices[idx]) == normalize_guess(answer)
        return False
    g = normalize_guess(raw)
    a = normalize_guess(answer)
    if not g or not a:
        return False
    return g == a
