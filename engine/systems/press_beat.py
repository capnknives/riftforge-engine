"""
press_beat.py -- News reporter desk job + field beat (engine framework).

Mirrors storm_chase.py: generic desk detection, photo inventory, story
briefs, and economy payouts. Games register hooks for path gates, on-duty
checks, room excitement scoring, and interview flavor.
"""

from __future__ import annotations

import random
import uuid

import engine.systems.economy as economy_wallet

JOB_ID = "news_reporter"
DESK_KEYS = frozenset({
    "lebanon:Lebanon Gazette",
    "Lebanon Gazette",
    "LG00001",
    "notbigville:News Office",
    "News Office",
    "NB00013",
})

MAX_PHOTOS = 8
PHOTO_PAY_PER_POINT = 2
DUTY_PHOTO_BONUS = 3
REWRITE_PAY_DOLLARS = 2
STORY_BASE_PAY_DOLLARS = 40
STORY_DUTY_BONUS_DOLLARS = 12
INTERVIEWS_NEEDED = 2
STORY_PHOTO_OPTIONAL = True

_STORY_TEMPLATES = (
    {
        "kind": "town_buzz",
        "title": "Main Street whispers",
        "blurb": (
            "The editor wants color from the square -- interview two locals "
            "who will talk on the record."
        ),
        "need_interviews": 2,
        "need_photos": 0,
    },
    {
        "kind": "storm_watch",
        "title": "Sky on the wire",
        "blurb": (
            "Storm season copy: snap something dramatic outdoors and get one "
            "quote from someone watching the clouds."
        ),
        "need_interviews": 1,
        "need_photos": 1,
    },
    {
        "kind": "crowd_scene",
        "title": "Room where it happened",
        "blurb": (
            "Photo first, then one interview while the moment is still warm."
        ),
        "need_interviews": 1,
        "need_photos": 1,
    },
)

_GENERIC_INTERVIEW_LINES = (
    '"I do not want my name in the paper," they say, then talk anyway.',
    '"You should have been here ten minutes ago," they shrug.',
    '"Lebanon is quieter than it looks," they insist -- unconvincingly.',
    '"Off the record? Fine. On the record? Still fine," they mutter.',
    '"Write that the coffee is bad and I will deny everything," they offer.',
)

_rng = random.Random()


def _ensure_photo_roll(character):
    """Return a mutable photo list on the character."""
    roll = getattr(character, "press_photos", None)
    if not isinstance(roll, list):
        roll = []
        character.press_photos = roll
    return roll


def _clear_story(character):
    """Wipe open story assignment state."""
    character.press_story_id = None
    character.press_story_brief = None
    character.press_story_flags = {}


def has_story(character):
    """True when a story brief is open."""
    return bool(getattr(character, "press_story_id", None))


def is_news_desk_room(room):
    """True when room is a Gazette / news desk (job site + board)."""
    if room is None:
        return False
    if room.key in DESK_KEYS:
        return True
    legacy = getattr(room, "legacy_key", None)
    if legacy and legacy in DESK_KEYS:
        return True
    jobs = tuple(getattr(room, "jobs", None) or ())
    return JOB_ID in jobs


def is_on_duty_reporter(character, game=None):
    """True when on-duty news_reporter at the desk."""
    from engine import hooks as hooks_mod

    if getattr(character, "job", None) != JOB_ID:
        return False
    if not is_news_desk_room(getattr(character, "location", None)):
        return False
    return hooks_mod.press_beat_is_on_duty(character, game=game)


def can_use_press_kit(character, game=None):
    """True when path or job grants field reporter verbs."""
    from engine import hooks as hooks_mod

    if hooks_mod.press_beat_is_reporter(character, game=game):
        return True
    path = (getattr(character, "bg_path", None) or "").strip().lower()
    if path == "reporter":
        return True
    if getattr(character, "job", None) == JOB_ID:
        return True
    return False


def refuse_press(character, game=None):
    """Plain refusal for gated reporter verbs."""
    if not can_use_press_kit(character, game):
        return (
            "You need the Reporter path or the news desk gig. "
            "Type 'help reporter'."
        )
    return "Start at the Gazette news desk for storyboard and sellphoto."


def _default_room_excitement(room, game):
    """Engine baseline photogenic score (0 = dull)."""
    if room is None:
        return 0, ""
    score = 0
    labels = []
    chars = list(room.characters())
    fighters = [
        c for c in chars
        if getattr(c, "target", None) is not None
    ]
    if fighters:
        score += 4
        labels.append("scuffle")
    if len(chars) >= 3:
        score += 2
        labels.append("crowd")
    if getattr(room, "outdoor", False):
        score += 1
        labels.append("street")
        try:
            from engine.systems import regional_weather as weather_mod

            w = weather_mod.weather_for_room(room, game, None)
            if w.get("tornado_watch"):
                score += 3
                labels.append("tornado watch")
        except Exception:
            pass
    npcs = [c for c in chars if getattr(c, "is_npc", False)]
    if npcs:
        score += 1
        labels.append("faces")
    if score <= 0:
        return 0, ""
    label = ", ".join(labels[:3])
    return score, label


def room_excitement(room, game):
    """Score + label for snap; hooks may override or extend."""
    from engine import hooks as hooks_mod

    hooked = hooks_mod.press_beat_room_excitement(room, game)
    if hooked is not None:
        if isinstance(hooked, tuple) and len(hooked) >= 2:
            return int(hooked[0] or 0), str(hooked[1] or "")
        if isinstance(hooked, dict):
            return int(hooked.get("score") or 0), str(hooked.get("label") or "")
    return _default_room_excitement(room, game)


def photograph(character, game):
    """Capture a photogenic moment in the current room."""
    return snap(character, game)


def snap(character, game):
    """Capture a photogenic moment in the current room."""
    if not can_use_press_kit(character, game):
        return False, refuse_press(character, game), None
    room = getattr(character, "location", None)
    if room is None:
        return False, "You are nowhere worth shooting.", None

    score, label = room_excitement(room, game)
    if score <= 0:
        return (
            False,
            "Nothing photogenic here yet -- find a crowd, a fight, or sky drama.",
            None,
        )

    roll = _ensure_photo_roll(character)
    if len(roll) >= MAX_PHOTOS:
        return (
            False,
            f"Film roll full ({MAX_PHOTOS}). sellphoto at the Gazette first.",
            None,
        )

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    room_key = getattr(room, "key", None) or "?"
    title = label or "street scene"
    photo = {
        "photo_id": uuid.uuid4().hex[:10],
        "label": title,
        "score": int(score),
        "room_key": room_key,
        "tick": ticks,
    }
    roll.append(photo)
    character.press_photos = roll

    if has_story(character):
        flags = getattr(character, "press_story_flags", None) or {}
        if not isinstance(flags, dict):
            flags = {}
        photos = list(flags.get("photos") or [])
        photos.append(photo["photo_id"])
        flags["photos"] = photos
        character.press_story_flags = flags

    msg = (
        f"[PHOTO] You catch it: {title} (score {score}). "
        f"Roll {len(roll)}/{MAX_PHOTOS}."
    )
    room_line = f"{character.key} raises a camera and fires off a shot."
    return True, msg, room_line


def photos_lines(character):
    """Format held photos for the photos command."""
    roll = _ensure_photo_roll(character)
    if not roll:
        return ["You have no unsold photos."]
    lines = ["Held photos (sellphoto at the Gazette):"]
    for i, photo in enumerate(roll, start=1):
        lines.append(
            f"  {i}. {photo.get('label', '?')} "
            f"(score {photo.get('score', 0)}) @ {photo.get('room_key', '?')}"
        )
    return lines


def sellphoto(character, game, index=None):
    """Sell one held photo at the news desk."""
    if not can_use_press_kit(character, game):
        return False, refuse_press(character, game), None
    room = getattr(character, "location", None)
    if not is_news_desk_room(room):
        return False, "Sell photos at the Gazette news desk.", None

    roll = _ensure_photo_roll(character)
    if not roll:
        return False, "No photos to sell. photograph something exciting first.", None

    if index is None:
        photo = roll.pop(0)
    else:
        try:
            slot = int(index) - 1
        except (TypeError, ValueError):
            return False, "Usage: sellphoto or sellphoto <number>.", None
        if slot < 0 or slot >= len(roll):
            return False, "That photo number is not on your roll.", None
        photo = roll.pop(slot)

    character.press_photos = roll
    score = int(photo.get("score") or 1)
    pay = max(1, score * PHOTO_PAY_PER_POINT)
    bonus = 0
    if is_on_duty_reporter(character, game):
        bonus = DUTY_PHOTO_BONUS
        pay += bonus
    economy_wallet.credit_wallet(character, dollars=pay)
    label = photo.get("label") or "shot"
    bonus_bit = f" (+{bonus} on-duty)" if bonus else ""
    msg = f"[PHOTO] Editor buys '{label}'. +{pay} dollars{bonus_bit}."
    room_line = f"{character.key} slides a print across the editor's desk."
    return True, msg, room_line


def copydesk(character, game):
    """On-duty desk fluff pay (storm research analogue)."""
    return rewrite(character, game)


def rewrite(character, game):
    """On-duty desk fluff pay (storm research analogue)."""
    if not is_on_duty_reporter(character, game):
        return False, (
            "Rewrite copy at the Gazette while on duty "
            "(work as news_reporter)."
        ), None
    from engine import hooks

    busy = hooks.utility_delay_begin(character, game, "press_rewrite")
    if busy:
        return False, busy, None
    economy_wallet.credit_wallet(character, dollars=REWRITE_PAY_DOLLARS)
    msg = (
        f"[DESK] You tighten a backlog paragraph. "
        f"+{REWRITE_PAY_DOLLARS} dollars."
    )
    room_line = f"{character.key} hammers a stubborn lede into shape."
    return True, msg, room_line


def board_lines(game, character=None):
    """Preview story opportunities at the desk."""
    lines = ["Gazette storyboard:"]
    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    for i, tmpl in enumerate(_STORY_TEMPLATES, start=1):
        lines.append(f"  {i}. {tmpl['title']} -- {tmpl['blurb']}")
    lines.append(
        "Commands: takestory | interview <name> | photograph | reportstory | "
        "abandonstory | sellphoto"
    )
    if character is not None and has_story(character):
        brief = getattr(character, "press_story_brief", None) or {}
        flags = getattr(character, "press_story_flags", None) or {}
        lines.append(
            f"Your story: {brief.get('title')} "
            f"(interviews {len(flags.get('interviews') or [])}/"
            f"{brief.get('need_interviews', INTERVIEWS_NEEDED)}, "
            f"photos {len(flags.get('photos') or [])}/"
            f"{brief.get('need_photos', 0)})"
        )
    lines.append(f"(board refresh tick {ticks})")
    return lines


def takestory(character, game, pick=None):
    """Accept a story brief at the Gazette."""
    room = getattr(character, "location", None)
    if not is_news_desk_room(room):
        return False, "Claim stories at the Gazette news desk.", None
    if not can_use_press_kit(character, game):
        return False, refuse_press(character, game), None
    if has_story(character):
        return False, "You already have an open story. reportstory or abandonstory.", None

    ticks = int(getattr(game, "game_time_ticks", 0) or 0)
    if pick is not None:
        try:
            idx = int(pick) - 1
        except (TypeError, ValueError):
            idx = ticks % len(_STORY_TEMPLATES)
    else:
        idx = ticks % len(_STORY_TEMPLATES)
    idx = max(0, min(len(_STORY_TEMPLATES) - 1, idx))
    tmpl = dict(_STORY_TEMPLATES[idx])
    story_id = f"story-{uuid.uuid4().hex[:8]}"
    brief = {
        "story_id": story_id,
        "title": tmpl["title"],
        "blurb": tmpl["blurb"],
        "kind": tmpl.get("kind"),
        "need_interviews": int(tmpl.get("need_interviews", INTERVIEWS_NEEDED)),
        "need_photos": int(tmpl.get("need_photos", 0)),
    }
    character.press_story_id = story_id
    character.press_story_brief = brief
    character.press_story_flags = {"interviews": [], "photos": []}
    msg = (
        f"[STORY] Accepted: {brief['title']}.\r\n"
        f"{brief['blurb']}\r\n"
        "interview locals, photograph if needed, reportstory here."
    )
    room_line = f"{character.key} tears a story card off the board."
    return True, msg, room_line


def _interview_line(character, target, game):
    """Flavor line for a successful interview."""
    from engine import hooks as hooks_mod

    hooked = hooks_mod.press_beat_interview_line(character, target, game)
    if hooked:
        return str(hooked)
    name = getattr(target, "key", "They")
    line = _rng.choice(_GENERIC_INTERVIEW_LINES)
    return f'{name} leans in. {line}'


def interview(character, target, game):
    """Interview someone in the room for the open story."""
    if not can_use_press_kit(character, game):
        return False, refuse_press(character, game), None
    if not has_story(character):
        return False, "takestory at the Gazette first.", None
    if target is None or target is character:
        return False, "Interview whom?", None
    room = getattr(character, "location", None)
    if room is None or target.location is not room:
        return False, "They are not here to interview.", None

    flags = getattr(character, "press_story_flags", None) or {}
    if not isinstance(flags, dict):
        flags = {}
    done = list(flags.get("interviews") or [])
    target_key = getattr(target, "key", None) or "?"
    if target_key in done:
        return False, "You already got a quote from them for this story.", None

    brief = getattr(character, "press_story_brief", None) or {}
    need = int(brief.get("need_interviews", INTERVIEWS_NEEDED))
    if len(done) >= need:
        return (
            False,
            "Enough interviews for this brief. reportstory at the Gazette.",
            None,
        )

    done.append(target_key)
    flags["interviews"] = done
    character.press_story_flags = flags
    quote = _interview_line(character, target, game)
    msg = (
        f"[INTERVIEW] Quote logged ({len(done)}/{need}).\r\n"
        f"{quote}"
    )
    room_line = (
        f"{character.key} jots notes while interviewing {target_key}."
    )
    return True, msg, room_line


def _story_ready(character):
    """True when brief requirements are satisfied."""
    if not has_story(character):
        return False
    brief = getattr(character, "press_story_brief", None) or {}
    flags = getattr(character, "press_story_flags", None) or {}
    need_i = int(brief.get("need_interviews", INTERVIEWS_NEEDED))
    need_p = int(brief.get("need_photos", 0))
    have_i = len(flags.get("interviews") or [])
    have_p = len(flags.get("photos") or [])
    if have_i < need_i:
        return False
    if need_p > 0 and have_p < need_p:
        return False
    return True


def reportstory(character, game):
    """File a completed story at the desk."""
    if not has_story(character):
        return False, "No open story. takestory at the Gazette.", None
    room = getattr(character, "location", None)
    if not is_news_desk_room(room):
        return False, "File stories at the Gazette news desk.", None
    if not _story_ready(character):
        brief = getattr(character, "press_story_brief", None) or {}
        flags = getattr(character, "press_story_flags", None) or {}
        return (
            False,
            "Story incomplete -- need "
            f"{brief.get('need_interviews', INTERVIEWS_NEEDED)} interviews "
            f"({len(flags.get('interviews') or [])} done) and "
            f"{brief.get('need_photos', 0)} photos "
            f"({len(flags.get('photos') or [])} done).",
            None,
        )

    brief = getattr(character, "press_story_brief", None) or {}
    pay = int(brief.get("pay_dollars") or STORY_BASE_PAY_DOLLARS)
    bonus = 0
    if is_on_duty_reporter(character, game):
        bonus = STORY_DUTY_BONUS_DOLLARS
        pay += bonus
    economy_wallet.credit_wallet(character, dollars=pay)
    title = brief.get("title") or "story"
    filed = int(getattr(character, "press_stories_filed", 0) or 0) + 1
    character.press_stories_filed = filed
    _clear_story(character)
    bonus_bit = f" (+{bonus} on-duty)" if bonus else ""
    msg = f"[STORY] Filed '{title}'. +{pay} dollars{bonus_bit}."
    room_line = f"{character.key} drops a finished story on the editor's desk."
    return True, msg, room_line


def abandonstory(character, game=None):
    """Drop the open story without pay."""
    if not has_story(character):
        return False, "No open story.", None
    _clear_story(character)
    return True, "[STORY] Story dropped.", None
