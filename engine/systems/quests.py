"""
quests.py -- generic authored quest progress on ``Character.quest_progress``.

State shape per quest id::

    {
      "status": "active" | "done" | "abandoned",
      "step": 0,
      "flags": {},
    }

``quest_progress`` is a dict of quest_id -> that blob (persisted).

Game-specific grants, spawns, inventory probes, and extra ``complete_when``
predicates register via this module's ``set_*`` / ``register_quest_predicate``
hooks (wired in ``supers/bootstrap.py`` for SUPERS). Zero ``supers`` imports.
"""

from __future__ import annotations

from engine.systems.quests_loader import (
    get_quest as loader_get_quest,
    list_quest_ids as loader_list_quest_ids,
    load_quests as loader_load_quests,
    validate_quest as loader_validate_quest,
)
import engine.systems.economy as economy_wallet
from engine.room_vnum import lookup_room, room_keys_match

# Thin alias namespace matching the old loader_mod call sites in this file.
loader_mod = None  # populated after import block for grep-friendly name


class _LoaderMod:
    get_quest = staticmethod(loader_get_quest)
    load_quests = staticmethod(loader_load_quests)
    list_quest_ids = staticmethod(loader_list_quest_ids)
    validate_quest = staticmethod(loader_validate_quest)


loader_mod = _LoaderMod()

# ---------------------------------------------------------------------------
# Game hook slots (SUPERS / basegame register at boot)
# ---------------------------------------------------------------------------

_quest_grant_handler = None
_quest_completion_reward_handler = None
_quest_spawn_handler = None
_quest_inventory_has = None
_quest_inventory_consume = None
_quest_begin_prep_handler = None
_quest_seek_tip_handler = None
_quest_idle_nudge_extra_handler = None
_quest_begin_side_effect = None
_quest_step_stamp_handler = None
_quest_npc_done_line_skip = None
_quest_empty_log_hint = None
_quest_no_offers_hint = None
_quest_predicates: dict = {}


def set_quest_grant_handler(fn):
    """Register fn(character, grant, game) for per-step ``grant`` blobs."""
    global _quest_grant_handler
    _quest_grant_handler = fn


def set_quest_completion_reward_handler(fn):
    """Register fn(character, data, game) -> list of paid summary lines."""
    global _quest_completion_reward_handler
    _quest_completion_reward_handler = fn


def set_quest_spawn_handler(fn):
    """Register fn(game, data) to place quest ``spawns`` drill mobs."""
    global _quest_spawn_handler
    _quest_spawn_handler = fn


def set_quest_inventory_has(fn):
    """Register fn(character, needle) -> bool for ``has_item`` / ``give_item``."""
    global _quest_inventory_has
    _quest_inventory_has = fn


def set_quest_inventory_consume(fn):
    """Register fn(character, needle) -> bool to remove one matching item."""
    global _quest_inventory_consume
    _quest_inventory_consume = fn


def set_quest_begin_prep_handler(fn):
    """Register fn(character, data, game) for quest JSON ``begin_prep``."""
    global _quest_begin_prep_handler
    _quest_begin_prep_handler = fn


def set_quest_seek_tip_handler(fn):
    """Register fn(character, game) -> optional seek-tutorial tip line."""
    global _quest_seek_tip_handler
    _quest_seek_tip_handler = fn


def set_quest_idle_nudge_extra_handler(fn):
    """Register fn(character, game) -> optional extra text for idle nudge."""
    global _quest_idle_nudge_extra_handler
    _quest_idle_nudge_extra_handler = fn


def set_quest_begin_side_effect(fn):
    """Register fn(character, data, game) after quest begin prep (game policy)."""
    global _quest_begin_side_effect
    _quest_begin_side_effect = fn


def set_quest_step_stamp_handler(fn):
    """Register fn(character, game) when a deferred opener step is queued."""
    global _quest_step_stamp_handler
    _quest_step_stamp_handler = fn


def set_quest_npc_done_line_skip(fn):
    """Register fn(npc) -> bool to skip ``done`` npc_lines (mission boards)."""
    global _quest_npc_done_line_skip
    _quest_npc_done_line_skip = fn


def set_quest_empty_log_hint(fn):
    """Register fn(character, *, screenreader=False) -> str for the empty
    quest-log flavor line. Pass None to restore the generic default."""
    global _quest_empty_log_hint
    _quest_empty_log_hint = fn


def set_quest_no_offers_hint(fn):
    """Register fn(character) -> str for the "nothing on offer here"
    fallback line. Pass None to restore the generic default."""
    global _quest_no_offers_hint
    _quest_no_offers_hint = fn


def register_quest_predicate(type_name, fn):
    """Register fn(character, when, event, payload) -> bool for custom types."""
    if type_name and fn is not None:
        _quest_predicates[str(type_name)] = fn


def _game_from_character(character, payload=None):
    """Resolve the live Game from notify payload / session / location."""
    game = (payload or {}).get("game")
    if game is not None:
        return game
    sess = getattr(character, "session", None)
    if sess is not None:
        game = getattr(sess, "game", None)
        if game is not None:
            return game
    loc = getattr(character, "location", None)
    return getattr(loc, "game", None) if loc is not None else None


def _progress(character):
    """Return mutable quest_progress dict (never None)."""
    box = getattr(character, "quest_progress", None)
    if box is None or not isinstance(box, dict):
        character.quest_progress = {}
        return character.quest_progress
    return box


def _entry(character, quest_id):
    """Return one quest progress entry or None."""
    return _progress(character).get(quest_id)


def _send(character, lines):
    """Send one string or a list of strings to the character's session."""
    session = getattr(character, "session", None)
    if session is None:
        return
    if isinstance(lines, str):
        session.send(lines)
        return
    for line in lines or []:
        if line:
            session.send(line)


def has_active_quest(character, quest_id=None):
    """True if any (or a named) quest is active."""
    prog = _progress(character)
    if quest_id:
        entry = prog.get(quest_id) or {}
        return entry.get("status") == "active"
    return any(
        (entry or {}).get("status") == "active" for entry in prog.values()
    )


def active_quest_ids(character):
    """List of active quest ids."""
    prog = _progress(character)
    return [
        qid
        for qid, entry in prog.items()
        if (entry or {}).get("status") == "active"
    ]


def _parse_step_index(entry):
    """Return a non-negative step index from quest progress, or 0 on bad data."""
    if not entry:
        return 0
    raw = entry.get("step", 0)
    try:
        idx = int(raw if raw is not None else 0)
    except (TypeError, ValueError):
        return 0
    return max(0, idx)


def _complete_quest(character, quest_id, game=None, *, announce=True):
    """Mark a quest done, unpin mentors, and pay completion rewards once."""
    data = loader_mod.get_quest(quest_id)
    entry = _entry(character, quest_id)
    if data is None or entry is None:
        return False
    if entry.get("status") == "done":
        return False
    entry["status"] = "done"
    pin_map = (entry.get("flags") or {}).get("pin_map") or data.get(
        "pin_npcs"
    )
    if pin_map and game is not None:
        _unpin_mentors(game, pin_map)
    flags = entry.setdefault("flags", {})
    if not flags.get("completion_rewards"):
        flags["completion_rewards"] = True
        if announce:
            _send(character, data.get("complete") or ["Case closed."])
        paid = _apply_rewards(character, data, game=game)
        if announce and paid and character.session:
            character.session.send(f"Paid: {', '.join(paid)}.")
    elif announce:
        _send(character, data.get("complete") or ["Case closed."])
    return True


def _heal_stale_quest_entry(character, quest_id, game=None, *, silent=False):
    """Finalize quests stuck active with step index past the last step."""
    entry = _entry(character, quest_id)
    data = loader_mod.get_quest(quest_id)
    if not entry or not data or entry.get("status") != "active":
        return False
    steps = data.get("steps") or []
    if not steps:
        return False
    idx = _parse_step_index(entry)
    if idx < len(steps):
        return False
    return _complete_quest(
        character, quest_id, game=game, announce=not silent,
    )


def heal_stale_quest_progress(game):
    """Boot heal: close opener quests left active past the final step index."""
    if game is None:
        return 0
    from engine.char_index import iter_characters

    healed = 0
    for char in iter_characters(game):
        if getattr(char, "is_npc", False):
            continue
        for qid in list(_progress(char).keys()):
            if _heal_stale_quest_entry(char, qid, game=game, silent=True):
                healed += 1
    return healed


def current_step(character, quest_id=None):
    """Return the active step dict for quest_id (or the sole active quest)."""
    if quest_id is None:
        ids = active_quest_ids(character)
        if len(ids) != 1:
            # Prefer the first active when multiple; callers can pass id.
            if not ids:
                return None
            quest_id = ids[0]
        else:
            quest_id = ids[0]
    entry = _entry(character, quest_id)
    if not entry or entry.get("status") != "active":
        return None
    data = loader_mod.get_quest(quest_id)
    if data is None:
        return None
    steps = data.get("steps") or []
    idx = _parse_step_index(entry)
    if idx < 0 or idx >= len(steps):
        game = _game_from_character(character)
        if idx >= len(steps):
            _heal_stale_quest_entry(
                character, quest_id, game=game, silent=False,
            )
        return None
    return steps[idx]


def list_offers_in_room(character, room):
    """Quests offered here that the character has not finished."""
    if room is None:
        return []
    room_key = getattr(room, "key", None) or ""
    prog = _progress(character)
    offers = []
    for qid, data in loader_mod.load_quests().items():
        entry = prog.get(qid) or {}
        if entry.get("status") in ("active", "done"):
            continue
        offer_rooms = data.get("offer_rooms") or []
        if offer_rooms and room_key not in offer_rooms:
            # Also allow offer when the giver NPC shares the room.
            giver = (data.get("giver_npc") or "").lower()
            present = False
            if giver:
                for obj in getattr(room, "contents", None) or []:
                    key = (getattr(obj, "key", None) or "").lower()
                    if giver in key or key in giver:
                        present = True
                        break
            if not present:
                continue
        offers.append(data)
    return offers


def offer_lines(character, room):
    """Player-facing lines for bare ``quests`` in a room."""
    lines = []
    active = active_quest_ids(character)
    if active:
        lines.append("Active cases:")
        for qid in active:
            data = loader_mod.get_quest(qid) or {}
            step = current_step(character, qid)
            hint = (step or {}).get("hint") or "(no hint)"
            lines.append(
                f"  - {data.get('title', qid)} [{qid}] -- hint: {hint}"
            )
    offers = list_offers_in_room(character, room)
    if offers:
        lines.append("Available here:")
        for data in offers:
            lines.append(
                f"  - {data.get('title')} [{data.get('id')}] -- "
                f"takequest {data.get('id')}"
            )
            summary = data.get("summary")
            if summary:
                lines.append(f"      {summary}")
    if not lines:
        lines.append(
            _quest_no_offers_hint(character)
            if _quest_no_offers_hint is not None
            else "No authored quests here right now. See 'help quests'."
        )
    return lines


def format_log_lines(character):
    """Full quest log (active + done) with phase / ACTIVE tags.

    Sighted path keeps compact ``[ACTIVE]`` tags. Screenreader path uses
    flat ``Label: value.`` lines with terminal punctuation for TTS
    (never meaning by layout alone).
    """
    sr = bool(getattr(character, "screenreader", False))
    prog = _progress(character)
    if not prog:
        if _quest_empty_log_hint is not None:
            return [_quest_empty_log_hint(character, screenreader=sr)]
        if sr:
            return [
                "Quest log empty. Look for authored quests around town, "
                "or type takequest after quests.",
            ]
        return [
            "Quest log empty. Look for folders around town "
            "('quests' / 'takequest')."
        ]
    game = _game_from_character(character)
    for qid in list(prog.keys()):
        _heal_stale_quest_entry(character, qid, game=game, silent=True)
    lines = ["Quest log."] if sr else ["Quest log:"]
    for qid in sorted(prog.keys()):
        entry = prog[qid] or {}
        data = loader_mod.get_quest(qid) or {}
        title = data.get("title") or qid
        status = entry.get("status") or "?"
        tag = {
            "active": "ACTIVE",
            "done": "DONE",
            "abandoned": "ABANDONED",
        }.get(status, status.upper())
        step_i = _parse_step_index(entry)
        steps = data.get("steps") or []
        total = len(steps)
        phase = ""
        if status == "active" and 0 <= step_i < total:
            phase = steps[step_i].get("phase") or ""
        if status == "done":
            progress_bit = (
                f"Steps: {total} (complete)."
                if sr
                else f"complete ({total} steps)"
            )
        elif status == "active" and total and step_i >= total:
            progress_bit = (
                f"Step: past final step (healing)."
                if sr
                else f"step past end (healing)"
            )
        else:
            progress_bit = (
                f"Step: {step_i + 1} of {total or '?'}."
                if sr
                else f"step {step_i + 1}/{total or '?'}"
            )
        if sr:
            lines.append(
                f"Status: {tag}. Title: {title}. Id: {qid}."
            )
            if phase:
                lines.append(f"Phase: {phase}.")
            lines.append(progress_bit)
        else:
            phase_bit = f" / {phase}" if phase else ""
            lines.append(
                f"  [{tag}] {title} ({qid}){phase_bit} "
                f"{progress_bit}"
            )
        if status == "active":
            step = current_step(character, qid)
            if step and step.get("hint"):
                if sr:
                    lines.append(f"Hint: {step['hint']}.")
                else:
                    lines.append(f"      hint: {step['hint']}")
            # Preview next few steps as LOCKED (plain tags, not color-alone).
            for later in steps[step_i + 1 : step_i + 4]:
                lid = later.get("id") or "?"
                lphase = later.get("phase") or ""
                bit = f"{lphase}: {lid}" if lphase else lid
                if sr:
                    lines.append(f"Locked next: {bit}.")
                else:
                    lines.append(f"      [LOCKED] {bit}")
    return lines


def status_lines(character, quest_id):
    """Short status for one quest."""
    data = loader_mod.get_quest(quest_id)
    if data is None:
        return [f"Unknown quest '{quest_id}'."]
    entry = _entry(character, quest_id)
    if not entry:
        return [f"'{data.get('title')}' is not on your log. try 'takequest {quest_id}'."]
    return format_log_lines(character)  # reuse; filtered enough for v1


def _apply_grant(character, grant, game=None):
    """Apply optional step/quest grant: dollars, favor, items, flags."""
    if not grant or not isinstance(grant, dict):
        return
    if _quest_grant_handler is not None:
        _quest_grant_handler(character, grant, game)
        return
    cash = None
    if "dollars" in grant or "cents" in grant:
        cash = {
            "dollars": grant.get("dollars", 0),
            "cents": grant.get("cents", 0),
        }
    elif "coins" in grant:
        cash = grant.get("coins")
    if cash is not None:
        economy_wallet.apply_cash_reward(character, cash)
        if character.session and economy_wallet.money_to_cents(cash):
            character.session.send(
                f"You receive {economy_wallet.format_money(cash)}."
            )
    if "flag" in grant:
        qid = getattr(character, "_quest_grant_target", None)
        if qid:
            entry = _entry(character, qid)
            if entry is not None:
                flags = entry.setdefault("flags", {})
                flags[grant["flag"]] = True


def _apply_rewards(character, data, game=None):
    """Pay quest completion rewards (mission-shaped list)."""
    if _quest_completion_reward_handler is not None:
        return _quest_completion_reward_handler(character, data, game)
    lines = []
    for reward in data.get("rewards") or []:
        rtype = reward.get("type")
        if economy_wallet.is_cash_loot_type(rtype):
            amount = reward.get("amount", 0)
            economy_wallet.apply_cash_reward(character, amount)
            lines.append(economy_wallet.format_money(amount))
        elif rtype == "growth":
            amount = round(float(reward.get("amount", 0) or 0), 2)
            character.growth = round(
                float(getattr(character, "growth", 0) or 0) + amount, 2
            )
            lines.append(f"{amount:g} banked growth")
    return lines


def _ensure_quest_spawns(game, data):
    """Place quest drill mobs listed in JSON ``spawns`` via game hook."""
    if game is None or not data or _quest_spawn_handler is None:
        return
    _quest_spawn_handler(game, data)


def ensure_active_quest_spawns_for_character(game, character):
    """Re-place drill motes for one character's active quests (login attach).

    Boot still uses :func:`ensure_active_quest_spawns` so offline Echoes
    with open Plane Temper / authored quests get motes after restart.
    """
    if game is None or character is None:
        return
    if getattr(character, "is_npc", False):
        return
    for qid in active_quest_ids(character):
        data = loader_mod.get_quest(qid)
        if data and (data.get("spawns") or []):
            _ensure_quest_spawns(game, data)


def ensure_active_quest_spawns(game):
    """Re-place drill motes for every active authored quest (boot).

    Idempotent. Safe when nobody holds an opener. Covers all Aspects'
    Plane Temper clones plus any other quest JSON that lists ``spawns``.
    """
    if game is None:
        return
    from engine.char_index import iter_characters

    seen_quest_ids = set()
    for char in iter_characters(game):
        if getattr(char, "is_npc", False):
            continue
        for qid in active_quest_ids(char):
            if qid in seen_quest_ids:
                continue
            seen_quest_ids.add(qid)
            data = loader_mod.get_quest(qid)
            if data and (data.get("spawns") or []):
                _ensure_quest_spawns(game, data)


def _apply_begin_prep(character, data, game=None):
    """Optional chargen prep flags from the quest JSON ``begin_prep`` list."""
    if _quest_begin_prep_handler is not None:
        _quest_begin_prep_handler(character, data, game)


def _inventory_has(character, needle):
    """True if inventory/gear contains catalog id or key fragment."""
    if not needle:
        return False
    if _quest_inventory_has is not None:
        return bool(_quest_inventory_has(character, needle))
    want = str(needle).lower()
    for item in list(getattr(character, "inventory", None) or []):
        catalog = (getattr(item, "catalog_id", None) or "").lower()
        key = (getattr(item, "key", None) or "").lower()
        if want == catalog or want in key or want in catalog:
            return True
    return False


def _consume_item(character, needle):
    """Remove one matching inventory/gear item. Return True if consumed."""
    if not needle:
        return False
    if _quest_inventory_consume is not None:
        return bool(_quest_inventory_consume(character, needle))
    want = str(needle).lower()
    inv = getattr(character, "inventory", None)
    if inv is None:
        return False
    for item in list(inv):
        catalog = (getattr(item, "catalog_id", None) or "").lower()
        key = (getattr(item, "key", None) or "").lower()
        if want == catalog or want in key or want in catalog:
            inv.remove(item)
            return True
    return False


def _hint_line(character, hint):
    """Format a step hint for sighted vs screenreader players."""
    text = (hint or "").strip()
    if not text:
        return ""
    if getattr(character, "screenreader", False):
        # Flat Label: value. so TTS does not rely on parentheses alone.
        if not text.endswith((".", "!", "?")):
            text = text + "."
        return f"Hint: {text}"
    return f"(hint: {hint})"


# Quiet once after the first real step on chargen openers -- not in intro.
_OPENER_META_LINE = (
    "This case is on your quest log -- type 'questlog' or 'hint' anytime."
)


def _enter_room_step_matches(character, when, game=None):
    """True when the character already stands in an enter_room step target."""
    if (when or {}).get("type") != "enter_room":
        return False
    loc = getattr(character, "location", None)
    if loc is None:
        return False
    got = getattr(loc, "key", None)
    if not got:
        return False
    game = game or _game_from_character(character)
    want = when.get("room")
    if want and room_keys_match(game, want, got):
        return True
    for room_key in when.get("rooms") or []:
        if room_keys_match(game, room_key, got):
            return True
    return False


def _maybe_auto_complete_enter_room(character, quest_id, game=None):
    """Advance when the player is already in the step's target room.

    ``walk library`` can deposit a Hunter in the stacks before the
    ``find_library`` step narrates; without this, ``enter_room`` never
  fires because they never leave and re-enter.
    """
    step = current_step(character, quest_id)
    if step is None:
        return False
    when = step.get("complete_when") or {}
    if when.get("type") == "any_of":
        for opt in when.get("options") or []:
            if isinstance(opt, dict) and _enter_room_step_matches(
                character, opt, game=game
            ):
                _advance(character, quest_id, game=game)
                return True
        return False
    if not _enter_room_step_matches(character, when, game=game):
        return False
    _advance(character, quest_id, game=game)
    return True


def _announce_current_step(character, quest_id, game=None, *, include_seek=False):
    """Send narrate + hint for the current step (one beat).

    Seek tips stay off the hot path -- idle nudge (and explicit ``hint``)
    cover Cadence walks so begin / advance do not dump a third paragraph.
    """
    step = current_step(character, quest_id)
    if step is None:
        return
    when = step.get("complete_when") or {}
    # ``start`` glue should already be skipped; never print it.
    if when.get("type") == "start":
        return
    _send(character, step.get("narrate") or [])
    hint = step.get("hint")
    if hint:
        _send(character, _hint_line(character, hint))
    try:
        if _quest_seek_tip_handler is not None:
            tip = _quest_seek_tip_handler(character, game)
            if tip:
                _send(character, tip)
    except Exception:
        pass
    _maybe_auto_complete_enter_room(character, quest_id, game=game)


def _skip_start_steps(character, quest_id, game=None):
    """Advance past leading ``complete_when: start`` steps without text."""
    data = loader_mod.get_quest(quest_id) or {}
    entry = _entry(character, quest_id)
    if entry is None:
        return
    steps = data.get("steps") or []
    # Cap the loop so a bad JSON chain of only-start steps cannot spin.
    for _ in range(len(steps) + 1):
        step = current_step(character, quest_id)
        if step is None:
            return
        when = step.get("complete_when") or {}
        if when.get("type") != "start":
            return
        # Silent index bump (no narrate) -- grant still applies if present.
        idx = _parse_step_index(entry)
        if step.get("grant"):
            character._quest_grant_target = quest_id
            _apply_grant(character, step.get("grant"), game=game)
            character._quest_grant_target = None
        entry["step"] = idx + 1
        if entry["step"] >= len(steps):
            _complete_quest(
                character, quest_id, game=game, announce=False,
            )
            return


def flush_pending_step_announce(character, game=None):
    """Send deferred first-step text (after chargen room look).

    Chargen openers print intro at begin, then wait until the automatic
    login ``look`` finishes so the room is on screen before the hint.
    """
    if character is None:
        return False
    flushed = False
    for qid in list(active_quest_ids(character)):
        entry = _entry(character, qid)
        if entry is None:
            continue
        flags = entry.setdefault("flags", {})
        if not flags.pop("pending_step_announce", None):
            continue
        _announce_current_step(character, qid, game=game, include_seek=False)
        flushed = True
    return flushed


def finish_chargen_quest_defer(character, game=None):
    """Tests / tools: flush deferred opener text and clear login-look skip.

    After ``begin_if_needed`` (defer_step), the next look notify is treated
    as the automatic login look. Call this when a smoke wants the *next*
    look to count as the player's foyer look.

    Real chargen: ``after_bare_look`` + the automatic ``look`` notify do
    this. FakeSession / notify-only smokes call this so the next ``look``
    advances the foyer step like a player typing look.
    """
    if character is None:
        return
    flush_pending_step_announce(character, game)
    for qid in list(active_quest_ids(character)):
        entry = _entry(character, qid)
        if entry is None:
            continue
        entry.setdefault("flags", {}).pop("ignore_next_look", None)


# Alias kept for older smoke call sites.
consume_login_look_skip = finish_chargen_quest_defer


def _maybe_opener_meta(character, quest_id):
    """One quiet questlog tip after the first foyer step on auto_start."""
    data = loader_mod.get_quest(quest_id) or {}
    if not data.get("auto_start"):
        return
    entry = _entry(character, quest_id)
    if entry is None:
        return
    flags = entry.setdefault("flags", {})
    if flags.get("opener_meta_told"):
        return
    # Only after leaving step 0 (look_around) -- first successful beat.
    if int(entry.get("step", 0) or 0) < 1:
        return
    flags["opener_meta_told"] = True
    _send(character, _OPENER_META_LINE)


def _advance(character, quest_id, game=None):
    """Move to the next step or complete the quest."""
    data = loader_mod.get_quest(quest_id)
    entry = _entry(character, quest_id)
    if data is None or entry is None:
        return
    steps = data.get("steps") or []
    idx = _parse_step_index(entry)
    step = steps[idx] if 0 <= idx < len(steps) else None
    if step and step.get("grant"):
        character._quest_grant_target = quest_id
        _apply_grant(character, step.get("grant"), game=game)
        character._quest_grant_target = None

    idx += 1
    entry["step"] = idx
    if idx >= len(steps):
        _complete_quest(character, quest_id, game=game, announce=True)
        return

    _maybe_opener_meta(character, quest_id)
    # Skip glue ``start`` steps without printing them.
    _skip_start_steps(character, quest_id, game=game)
    if (_entry(character, quest_id) or {}).get("status") == "done":
        return
    # One beat: narrate + hint. Seek waits for idle nudge / explicit hint.
    _announce_current_step(character, quest_id, game=game, include_seek=False)


def begin(character, quest_id, game=None, *, force=False, defer_step=False):
    """Start a quest for the character. Returns (ok, message).

    ``defer_step=True`` (chargen auto_start): send ``intro`` only, then
    hold first-step narrate/hint until ``flush_pending_step_announce``
    (after the login room look). ``takequest`` / GM grant keep
    ``defer_step=False`` so the player gets the next beat immediately.
    """
    data = loader_mod.get_quest(quest_id)
    if data is None:
        return False, f"Unknown quest '{quest_id}'."
    prog = _progress(character)
    entry = prog.get(quest_id)
    if entry and entry.get("status") == "active" and not force:
        return False, "You already have that case open. See 'questlog'."
    if entry and entry.get("status") == "done" and not force:
        return False, "You already closed that case."
    prog[quest_id] = {"status": "active", "step": 0, "flags": {}}
    pin_map = data.get("pin_npcs") or {}
    if pin_map and game is not None:
        _pin_mentors(game, pin_map)
        prog[quest_id]["flags"]["pin_map"] = dict(pin_map)
    # Drill motes / Charge headroom before intro (Elemental openers).
    _ensure_quest_spawns(game, data)
    _apply_begin_prep(character, data, game=game)
    if _quest_begin_side_effect is not None:
        _quest_begin_side_effect(character, data, game)
    # Intro alone -- never pile step text / seek tips on the same dump.
    _send(character, data.get("intro") or [])
    steps = data.get("steps") or []
    if not steps:
        prog[quest_id]["status"] = "done"
        return True, f"Case '{data.get('title')}' had no steps -- marked done."
    _skip_start_steps(character, quest_id, game=game)
    entry = _entry(character, quest_id)
    if entry is None or entry.get("status") == "done":
        return True, f"You open the case: {data.get('title')}."
    flags = entry.setdefault("flags", {})
    if defer_step:
        # Login auto-look must not clear the foyer look step before the
        # player sees the hint -- ignore that one look completion.
        flags["pending_step_announce"] = True
        flags["ignore_next_look"] = True
        try:
            if _quest_step_stamp_handler is not None:
                _quest_step_stamp_handler(character, game)
        except Exception:
            pass
    else:
        _announce_current_step(
            character, quest_id, game=game, include_seek=False
        )
    return True, f"You open the case: {data.get('title')}."


def grant_quest(character, quest_id, game=None):
    """GM force-start (re-opens even if done)."""
    return begin(character, quest_id, game=game, force=True)


def abandon(character, quest_id=None, game=None, *, confirm=False):
    """Abandon one active quest (or the only active one).

    Family Business and other gated openers require ``confirm=True``
    (player typed ``abandonquest confirm``).
    """
    ids = active_quest_ids(character)
    if quest_id is None:
        if len(ids) == 1:
            quest_id = ids[0]
        elif not ids:
            return False, "You have no active authored quest to abandon."
        else:
            return False, (
                "Several cases are open -- abandonquest <id> confirm. "
                f"Active: {', '.join(ids)}"
            )
    entry = _entry(character, quest_id)
    if not entry or entry.get("status") != "active":
        return False, f"No active quest '{quest_id}'."
    data = loader_mod.get_quest(quest_id) or {}
    # Soft openers with gates ask for confirm (Family Business).
    if data.get("auto_start") and not confirm:
        return (
            False,
            f"Abandon '{data.get('title', quest_id)}'? Type "
            f"'abandonquest {quest_id} confirm' (or 'abandonquest confirm' "
            "when it is your only open case).",
        )
    pin_map = (entry.get("flags") or {}).get("pin_map") or data.get("pin_npcs")
    if pin_map and game is not None:
        _unpin_mentors(game, pin_map)
    entry["status"] = "abandoned"
    return True, f"You abandon the case: {data.get('title', quest_id)}."


def reset_quest(character, quest_id):
    """GM: wipe one quest entry entirely."""
    prog = _progress(character)
    if quest_id not in prog:
        return False, f"No quest progress for '{quest_id}'."
    del prog[quest_id]
    return True, f"Cleared quest progress for '{quest_id}'."


def advance_for_gm(character, quest_id, game=None):
    """GM: force-advance one step."""
    if not has_active_quest(character, quest_id):
        ok, msg = grant_quest(character, quest_id, game=game)
        if not ok:
            return False, msg
        # grant may have auto-advanced start steps already.
        if not has_active_quest(character, quest_id):
            return True, f"Granted and completed '{quest_id}'."
        return True, f"Granted '{quest_id}' (use advance again to step)."
    _advance(character, quest_id, game=game)
    if has_active_quest(character, quest_id):
        step = current_step(character, quest_id)
        return True, (
            f"Advanced '{quest_id}' to step "
            f"{(step or {}).get('id', '?')}."
        )
    return True, f"Advanced '{quest_id}' -- case complete."


def _match_when(character, when, event, payload):
    """Return True if the step's complete_when matches this event."""
    kind = when.get("type")
    custom = _quest_predicates.get(kind)
    if custom is not None:
        return custom(character, when, event, payload)
    if kind == "any_of":
        for opt in when.get("options") or []:
            if isinstance(opt, dict) and _match_when(
                character, opt, event, payload
            ):
                return True
        return False
    if kind == "enter_room" and event == "enter_room":
        want = when.get("room")
        got = payload.get("room_key")
        rooms = when.get("rooms") or []
        game = _game_from_character(character, payload)
        if want and room_keys_match(game, want, got):
            return True
        for room_key in rooms:
            if room_keys_match(game, room_key, got):
                return True
        return False
    if kind == "verb" and event == "verb":
        want = (when.get("verb") or "").lower()
        got = (payload.get("verb") or "").lower()
        aliases = [a.lower() for a in (when.get("aliases") or [])]
        return got == want or got in aliases
    if kind == "any_verbs" and event == "verb":
        got = (payload.get("verb") or "").lower()
        verbs = [str(v).lower() for v in (when.get("verbs") or [])]
        return got in verbs
    if kind == "true_form" and event == "verb":
        if (payload.get("verb") or "").lower() == "form":
            return bool(getattr(character, "true_form", False))
        return False
    if kind == "talk_npc" and event == "talk_npc":
        want = (when.get("npc") or "").lower()
        got = (payload.get("npc") or "").lower()
        if not want:
            return True
        return want in got or got in want
    if kind == "kill_tag" and event == "kill_tag":
        return bool(when.get("tag")) and when.get("tag") == payload.get("tag")
    if kind == "flag" and event == "flag":
        # payload may name quest_id; scan active.
        want = when.get("flag")
        for qid in active_quest_ids(character):
            entry = _entry(character, qid) or {}
            flags = entry.get("flags") or {}
            if want and flags.get(want):
                return True
        return False
    if kind == "item" and event == "item":
        want = (when.get("item") or "").lower()
        got = (payload.get("item") or "").lower()
        return bool(want) and want in got
    if kind == "has_item" and event in (
        "talk_npc", "has_item", "verb", "enter_room", "buy", "gear",
    ):
        return _inventory_has(character, when.get("item"))
    if kind == "give_item" and event == "talk_npc":
        # Deliver by talking to the NPC while holding the item.
        want_npc = (when.get("npc") or "").lower()
        got = (payload.get("npc") or "").lower()
        if want_npc and not (want_npc in got or got in want_npc):
            return False
        item = when.get("item")
        if not _inventory_has(character, item):
            return False
        return _consume_item(character, item)
    if kind == "takehunt" and event == "takehunt":
        return True
    if kind == "takedungeon" and event == "takedungeon":
        return True
    if kind == "haunt_claim" and event == "haunt_claim":
        return True
    if kind == "investigate" and event == "investigate":
        return True
    if kind == "claim_home" and event == "claim_home":
        return True
    if kind == "rent" and event == "rent":
        return True
    if kind == "buy" and event == "buy":
        want = (when.get("item") or "").lower()
        got = (payload.get("item") or "").lower()
        if want:
            return want in got
        return True
    if kind == "help_topic" and event == "help_topic":
        want = (when.get("topic") or "").lower()
        got = (payload.get("topic") or "").lower()
        return bool(want) and want == got
    if kind == "reporthunt" and event == "reporthunt":
        return True
    if kind == "reportdungeon" and event == "reportdungeon":
        return True
    return False


def notify(character, event, **payload):
    """Try to advance any active quest whose current step matches `event`."""
    if character is None:
        return
    if getattr(character, "is_npc", False):
        return
    game = payload.get("game")
    for qid in list(active_quest_ids(character)):
        entry = _entry(character, qid)
        flags = (entry or {}).setdefault("flags", {}) if entry else {}
        # Chargen auto-look: show the room, flush deferred hint, do not
        # clear the foyer look step on that one automatic look.
        if (
            flags.get("ignore_next_look")
            and event == "verb"
            and (payload.get("verb") or "").lower() in ("look", "l")
        ):
            flags["ignore_next_look"] = False
            flush_pending_step_announce(character, game)
            continue
        step = current_step(character, qid)
        if step is None:
            continue
        when = step.get("complete_when") or {}
        if _match_when(character, when, event, payload):
            _advance(character, qid, game=game)


def allows_talk(character, npc):
    """True when ``talk`` may bypass the peaceful-NPC gate for this pair.

    Immersion mentors (Dean, Sam, …) are player-shaped Echoes
    (``is_npc`` / ``peaceful`` false) but authored openers still need
    ``talk Dean`` / ``talk Sam``. Allow when an active quest has
    ``npc_lines`` for their key, or they are listed in that quest's
    pin map / giver. Offer-room lines also count so a giver can be
    talked to before takequest.
    """
    if character is None or npc is None:
        return False
    # Cheapest path: any authored talk line for this pair.
    if npc_talk_line(character, npc) is not None:
        return True
    npc_key = getattr(npc, "key", None) or ""
    if not npc_key:
        return False
    want = npc_key.lower()
    for qid in active_quest_ids(character):
        data = loader_mod.get_quest(qid) or {}
        if (data.get("giver_npc") or "").lower() == want:
            return True
        pin_map = data.get("pin_npcs") or {}
        if any(str(k).lower() == want for k in pin_map):
            return True
        lines = data.get("npc_lines") or {}
        if any(str(k).lower() == want for k in lines):
            return True
    room = getattr(character, "location", None)
    for data in list_offers_in_room(character, room):
        if (data.get("giver_npc") or "").lower() == want:
            return True
        lines = data.get("npc_lines") or {}
        if any(str(k).lower() == want for k in lines):
            return True
    return False


def npc_talk_line(character, npc):
    """Return a quest-gated talk line for npc, or None to fall through.

    Prefers: offer (if offered here and not started) → active → done.
    """
    if character is None or npc is None:
        return None
    npc_key = getattr(npc, "key", None) or ""
    room = getattr(character, "location", None)
    # Active quest lines first.
    for qid in active_quest_ids(character):
        data = loader_mod.get_quest(qid) or {}
        lines = (data.get("npc_lines") or {}).get(npc_key) or {}
        # Also try giver_npc key match.
        if not lines and data.get("giver_npc"):
            if (data.get("giver_npc") or "").lower() in npc_key.lower():
                lines = (data.get("npc_lines") or {}).get(
                    data.get("giver_npc")
                ) or {}
        if not lines:
            continue
        step = current_step(character, qid)
        step_id = (step or {}).get("id")
        if step_id and lines.get(f"step:{step_id}"):
            return lines[f"step:{step_id}"]
        if lines.get("active"):
            return lines["active"]
    # Offer line when standing in an offer room / with giver.
    for data in list_offers_in_room(character, room):
        lines = (data.get("npc_lines") or {}).get(npc_key) or {}
        if not lines and data.get("giver_npc"):
            if (data.get("giver_npc") or "").lower() in npc_key.lower():
                lines = (data.get("npc_lines") or {}).get(
                    data.get("giver_npc")
                ) or {}
        if lines.get("offer"):
            return lines["offer"]
    # Done flavor -- optional skip (mission boards, etc.).
    if _quest_npc_done_line_skip is not None and _quest_npc_done_line_skip(npc):
        return None
    prog = _progress(character)
    for qid, entry in prog.items():
        if (entry or {}).get("status") != "done":
            continue
        data = loader_mod.get_quest(qid) or {}
        lines = (data.get("npc_lines") or {}).get(npc_key) or {}
        if not lines and data.get("giver_npc"):
            if (data.get("giver_npc") or "").lower() in npc_key.lower():
                lines = (data.get("npc_lines") or {}).get(
                    data.get("giver_npc")
                ) or {}
        if lines.get("done"):
            return lines["done"]
    return None


def show_hint(character):
    """Reprint hints for all active authored quests (and fall through).

    Screenreader: flat ``Case:`` / ``Phase:`` / ``Hint:`` lines with
    terminal punctuation. Sighted: short dashed headers (decorative only;
    hint text still carries the meaning).
    """
    ids = active_quest_ids(character)
    if not ids:
        return False
    sr = bool(getattr(character, "screenreader", False))
    game = _game_from_character(character)
    for qid in ids:
        _maybe_auto_complete_enter_room(character, qid, game=game)
        data = loader_mod.get_quest(qid) or {}
        step = current_step(character, qid)
        if step is None:
            continue
        phase = (step or {}).get("phase")
        title = data.get("title", qid)
        if sr:
            _send(character, f"Case: {title}.")
            if phase:
                _send(character, f"Phase: {phase}.")
        else:
            header = f"-- {title}"
            if phase:
                header += f" / {phase}"
            header += " --"
            _send(character, header)
        narrate = (step or {}).get("narrate") or []
        _send(character, narrate)
        hint = (step or {}).get("hint")
        if hint:
            _send(character, _hint_line(character, hint))
        else:
            if sr:
                _send(character, "Hint: none for this step.")
            else:
                _send(character, "(No extra hint for this step.)")
        # Optional recovery line for travel-heavy opener steps (lodging,
        # finding Bobby, haunt intro that used to send people road-tripping).
        stuck = (step or {}).get("stuck_hint")
        if stuck:
            if sr:
                _send(character, f"If stuck: {stuck}")
            else:
                _send(character, f"(If stuck: {stuck})")
        # After lingering, Cadence may suggest seek tutorial (main).
        try:
            if _quest_seek_tip_handler is not None:
                tip = _quest_seek_tip_handler(character, None)
                if tip:
                    if sr and not tip.endswith((".", "!", "?")):
                        tip = tip + "."
                    _send(character, tip)
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# Soft-but-strict gate, mentor pins, auto-start, restart, idle nudge
# ---------------------------------------------------------------------------

# Always allowed even when a foyer step sets gate.block_offtask.
# leave/out: newbies who board a car mid-opener must be able to climb out
# without fighting the soft lock (Coris-style vehicle strand).
ALWAYS_ALLOWED = frozenset({
    "look", "l", "exits",
    "score", "sc",
    "inventory", "i", "inv", "eq",
    "help", "commands", "hint", "tutorial",
    "quests", "questlog", "objectives",
    "takequest", "abandonquest", "restartquest",
    # Lost opener rescue -- Cadence walks the current lesson step.
    "seek",
    "ooc", "bug", "suggest",
    "config", "screenreader",
    "who", "time", "changes",
    "quit", "logout",
    "leave", "out",
})

# ~75s -- reprint active hint while a gated opener is live. Absolute
# deadline (stamped against game_time_ticks) -- session pacing, not
# calendar. Converted via ticks_for_wall_seconds at the live gm clock
# scale wherever consumed.
NUDGE_COOLDOWN_SECONDS = 75.0


def heal_active_quest_pins(game):
    """Boot heal: re-apply opener mentor pins after roster home relocates.

    ``cadence_boot.heal_roster_fixture_positions`` parks Bobby in Overflow;
    active Family Business (and similar) quests need him back on the board.
    Idempotent every restart.
    """
    if game is None:
        return 0
    from engine.char_index import iter_characters

    pinned = 0
    seen = set()
    for char in iter_characters(game):
        if getattr(char, "is_npc", False):
            continue
        for qid in active_quest_ids(char):
            entry = _entry(char, qid)
            if not entry or entry.get("status") != "active":
                continue
            pin_map = (entry.get("flags") or {}).get("pin_map")
            if not pin_map:
                data = loader_mod.get_quest(qid) or {}
                pin_map = data.get("pin_npcs") or {}
            if not pin_map:
                continue
            sig = tuple(sorted(pin_map.items()))
            if sig in seen:
                continue
            seen.add(sig)
            _pin_mentors(game, pin_map)
            pinned += 1
    return pinned


def _pin_mentors(game, pin_map):
    """Park named NPCs in pin rooms and mark stay_home so talk cannot soft-lock."""
    if not game or not pin_map:
        return
    for npc_key, room_key in pin_map.items():
        npc = None
        find = getattr(game, "find_character", None)
        if callable(find):
            npc = find(npc_key)
        if npc is None:
            continue
        room = lookup_room(game, room_key)
        if room is None:
            continue
        # Remember prior park so abandon/complete can restore.
        if not getattr(npc, "_quest_pin_restore", None):
            npc._quest_pin_restore = {
                "home_room_key": getattr(npc, "home_room_key", None),
                "stay_home": bool(getattr(npc, "stay_home", False)),
                "room_key": getattr(
                    getattr(npc, "location", None), "key", None
                ),
            }
        npc.stay_home = True
        npc.home_room_key = room_key
        if getattr(npc, "location", None) is not room:
            npc.move_to(room)


def _unpin_mentors(game, pin_map):
    """Restore mentors after quest complete / abandon / restart wipe."""
    if not game or not pin_map:
        return
    rooms = getattr(game, "rooms", None) or {}
    for npc_key in pin_map:
        find = getattr(game, "find_character", None)
        npc = find(npc_key) if callable(find) else None
        if npc is None:
            continue
        restore = getattr(npc, "_quest_pin_restore", None)
        if not isinstance(restore, dict):
            continue
        npc.stay_home = bool(restore.get("stay_home", False))
        home_key = restore.get("home_room_key")
        npc.home_room_key = home_key
        dest_key = home_key or restore.get("room_key")
        dest = lookup_room(game, dest_key) if dest_key else None
        if dest is None and dest_key:
            dest = rooms.get(dest_key)
        if dest is not None and getattr(npc, "location", None) is not dest:
            npc.move_to(dest)
        npc._quest_pin_restore = None


def active_gate_step(character):
    """Return (quest_id, step) for the first active step with a gate, else None."""
    for qid in active_quest_ids(character):
        step = current_step(character, qid)
        if step and isinstance(step.get("gate"), dict):
            return qid, step
    return None


def pre_dispatch_allowed(character, verb, *, is_move=False):
    """Soft-strict gate: (allowed: bool, nudge_or_None).

    When the active step sets ``gate.block_offtask``, only ALWAYS_ALLOWED,
    ``gate.allow_extra``, the required verb/aliases, and (when
    ``allow_movement``) walks may run. Otherwise every verb is allowed.
    """
    if character is None or getattr(character, "is_npc", False):
        return True, None
    pair = active_gate_step(character)
    if pair is None:
        return True, None
    _qid, step = pair
    gate = step.get("gate") or {}
    if not gate.get("block_offtask"):
        return True, None
    verb_l = (verb or "").lower()
    allowed = set(ALWAYS_ALLOWED)
    for extra in gate.get("allow_extra") or []:
        allowed.add(str(extra).lower())
    req = (gate.get("requires_verb") or "").lower()
    if req:
        allowed.add(req)
    for alias in gate.get("aliases") or []:
        allowed.add(str(alias).lower())
    if is_move:
        if gate.get("allow_movement"):
            return True, None
        hint = step.get("hint") or req or "the current objective"
        return False, (
            f"[JOURNAL] Stay on task -- {hint}. "
            "(Type 'hint' or 'questlog'; help / look / exits still work.)"
        )
    if verb_l in allowed:
        return True, None
    hint = step.get("hint") or req or "the current objective"
    return False, (
        f"[JOURNAL] Soft lock -- finish this first: {hint}. "
        "(Type 'hint' or 'questlog'.)"
    )


def restart(character, quest_id=None, game=None, *, confirm=False):
    """Wipe progress and re-run begin (pins + intro). Requires confirm."""
    ids = active_quest_ids(character)
    if quest_id is None:
        if len(ids) == 1:
            quest_id = ids[0]
        elif not ids:
            return False, (
                "No active case to restart. "
                "Type 'restartquest <id> confirm' (e.g. family_business)."
            )
        else:
            return False, (
                "Several cases are open -- restartquest <id> confirm. "
                f"Active: {', '.join(ids)}"
            )
    data = loader_mod.get_quest(quest_id)
    if data is None:
        return False, f"Unknown quest '{quest_id}'."
    if not confirm:
        return (
            False,
            f"Restart '{data.get('title', quest_id)}' from the top? Type "
            f"'restartquest {quest_id} confirm'.",
        )
    entry = _entry(character, quest_id)
    if entry and entry.get("status") == "active":
        pin_map = (entry.get("flags") or {}).get("pin_map") or data.get(
            "pin_npcs"
        )
        if pin_map and game is not None:
            _unpin_mentors(game, pin_map)
    prog = _progress(character)
    if quest_id in prog:
        del prog[quest_id]
    return begin(character, quest_id, game=game, force=True)


def tick_idle_nudge(game):
    """Heartbeat: soft [JOURNAL] reprint for gated / hinted openers."""
    if game is None:
        return
    now = int(getattr(game, "game_time_ticks", 0) or 0)
    from engine import game_clock_tuning as clock_mod
    nudge_cooldown = clock_mod.ticks_for_wall_seconds(NUDGE_COOLDOWN_SECONDS, game)
    for char in list(getattr(game, "characters", ()) or []):
        if getattr(char, "is_npc", False):
            continue
        if getattr(char, "session", None) is None:
            continue
        for qid in active_quest_ids(char):
            step = current_step(char, qid)
            if step is None:
                continue
            hint = step.get("hint")
            if not hint:
                continue
            gate = step.get("gate") or {}
            # Nudge foyer soft-locks always; also nudge auto_start openers.
            data = loader_mod.get_quest(qid) or {}
            if not gate.get("block_offtask") and not data.get("auto_start"):
                continue
            entry = _entry(char, qid) or {}
            flags = entry.setdefault("flags", {})
            last = int(flags.get("nudge_tick") or 0)
            if last and (now - last) < nudge_cooldown:
                continue
            flags["nudge_tick"] = now
            # Travel recovery (stuck_hint) plus optional seek tutorial tip.
            seek_bit = ""
            try:
                if _quest_idle_nudge_extra_handler is not None:
                    extra = _quest_idle_nudge_extra_handler(char, game)
                    if extra:
                        seek_bit = extra
            except Exception:
                seek_bit = ""
            stuck = (step or {}).get("stuck_hint")
            if stuck:
                _send(
                    char,
                    f"[JOURNAL] Still on this: {hint} "
                    f"-- {stuck}{seek_bit}",
                )
            else:
                _send(
                    char,
                    f"[JOURNAL] Still on this: {hint}.{seek_bit} "
                    "(or type 'hint' / 'questlog').",
                )
            break  # one nudge per character per tick pass
