"""
active_combat.py -- timestamp-buffered twitch combat registry + tick drain.

docs/plans/fast_paced_combat_engine.md. Separate from ``combat_engine.py``
(decision #3): swing engines stay untouched. This module owns:

  * per-character offense/aim FIFO (capped -- §10.6)
  * open Telegraph registry on defenders (§10.3)
  * global earliest-timestamp tick drain (§10.2)
  * manual-then-auto defense sequencing (§10.4)
  * tick-scoped prose compression (§10.9)
  * injectable ``now_fn`` clock seam (§10.5)

Hard rule 5: ``build_brief`` / ``apply_brief`` are pure-then-mutate;
``narrate`` is a separate optional step. Hard rule 8: never skip
auto-defense because ``session is None`` (§10.7).
"""

from __future__ import annotations

import itertools
import time

from engine.systems import active_combat_defense as defense_mod
from engine.systems import readiness as readiness_mod

# --- Tunables --------------------------------------------------------------

OFFENSE_QUEUE_CAP = 3
DEFAULT_TELEGRAPH_WINDOW = 1.0  # seconds -- mid of the doc's 0.8–1.5 range
ENGINE_ID_DEFAULT = "kinetic"

# Verb kit profiles: balance cost, base damage mult, ground-only, status tag.
VERB_PROFILES = {
    "jab": {
        "balance_cost": 1.0,
        "damage": 4.0,
        "requires_ground": False,
        "status": None,
    },
    "punch": {
        "balance_cost": 1.8,
        "damage": 8.0,
        "requires_ground": False,
        "status": None,
    },
    "sweep": {
        "balance_cost": 2.2,
        "damage": 6.5,
        "requires_ground": True,
        "status": "prone",
    },
    "uppercut": {
        "balance_cost": 2.8,
        "damage": 12.0,
        "requires_ground": False,
        "status": "staggered",
    },
    "kick": {
        "balance_cost": 2.0,
        "damage": 7.0,
        "requires_ground": True,
        "status": None,
    },
    "legkick": {
        "balance_cost": 2.0,
        "damage": 7.0,
        "requires_ground": True,
        "status": None,
    },
    "headbutt": {
        "balance_cost": 2.4,
        "damage": 9.0,
        "requires_ground": False,
        "status": "staggered",
    },
    "grab": {
        "balance_cost": 2.0,
        "damage": 2.0,
        "requires_ground": False,
        "status": "grabbed",
    },
    "fire": {
        "balance_cost": 2.0,
        "damage": 14.0,
        "requires_ground": False,
        "status": None,
    },
}

AIM_EQUILIBRIUM_COST = 1.5
LOAD_BALANCE_COST = 1.2

QUEUE_ATTR = "active_combat_queue"
TELEGRAPHS_ATTR = "open_telegraphs"

_KIND_OFFENSE = "offense"
_KIND_MANUAL_DEFENSE = "manual_defense"
_KIND_AIM = "aim"       # firearm sight line only (not melee)
_KIND_LOAD = "load"     # chamber a round
_KIND_FIRE = "fire"     # discharge chambered round
_KIND_CLEAR = "clear_queue"

_telegraph_ids = itertools.count(1)

# id -> {build_brief, apply_brief, narrate} -- same shape as combat_engine.
_ENGINES = {}


def register_active_combat_engine(engine_id, *, build_brief, apply_brief,
                                  narrate=None):
    """Register a pluggable active-combat engine under ``engine_id``.

    Idempotent overwrite on re-register (hot-reload / smoke re-import).
    """
    _ENGINES[str(engine_id)] = {
        "build_brief": build_brief,
        "apply_brief": apply_brief,
        "narrate": narrate,
    }


def known_active_combat_engines():
    """Frozen set of every registered active-combat engine id."""
    return frozenset(_ENGINES)


def get_active_combat_engine(engine_id):
    """Return the registration dict for ``engine_id``, or None."""
    return _ENGINES.get(str(engine_id))


def _now(now_fn):
    return (now_fn or time.monotonic)()


def _name(character):
    return (
        getattr(character, "key", None)
        or getattr(character, "name", None)
        or "someone"
    )


def ensure_defaults(character):
    """Stamp queue / telegraph / defense / readiness attrs (idempotent)."""
    if getattr(character, QUEUE_ATTR, None) is None:
        setattr(character, QUEUE_ATTR, [])
    if getattr(character, TELEGRAPHS_ATTR, None) is None:
        setattr(character, TELEGRAPHS_ATTR, {})
    readiness_mod.ensure_defaults(character)
    defense_mod.ensure_defaults(character)


def get_queue(character):
    """Return the character's offense/aim FIFO (creates empty if missing)."""
    ensure_defaults(character)
    return getattr(character, QUEUE_ATTR)


def clear_queue(character):
    """Drop every pending queued command. Does NOT cancel open telegraphs.

    Returns how many commands were dropped. Safe on Echoes (§10.7).
    """
    ensure_defaults(character)
    queue = getattr(character, QUEUE_ATTR)
    n = len(queue)
    queue.clear()
    return n


def enqueue(character, command, *, now_fn=None):
    """Append a stamped command to ``character``'s FIFO.

    Offense/aim commands respect OFFENSE_QUEUE_CAP (§10.6) and return
    ``(False, "[QUEUE FULL] ...")`` when capped. Manual defense and
    clear_queue are never capped (defense must stay reachable).

    ``command`` is a dict; this function stamps ``timestamp`` if missing.
    """
    ensure_defaults(character)
    kind = command.get("kind")
    queue = get_queue(character)
    if kind in (_KIND_OFFENSE, _KIND_AIM, _KIND_LOAD, _KIND_FIRE):
        if len(queue) >= OFFENSE_QUEUE_CAP:
            return False, (
                f"[QUEUE FULL] At most {OFFENSE_QUEUE_CAP} pending actions "
                f"(type -- to clear)."
            )
    if "timestamp" not in command:
        command["timestamp"] = _now(now_fn)
    queue.append(command)
    return True, None


def launch_offense_immediately(character, verb, target, *, game=None,
                               now_fn=None):
    """Fire an opener offense now -- skip FIFO (first strike out of combat).

    Spends Balance and opens a telegraph immediately. Subsequent strikes
    should use ``enqueue`` so the queue + cooldown loop applies.
    """
    ensure_defaults(character)
    profile = VERB_PROFILES.get(verb, VERB_PROFILES["punch"])
    if not readiness_mod.is_ready(character, "balance", now_fn=now_fn):
        return enqueue(character, {
            "kind": _KIND_OFFENSE,
            "verb": verb,
            "target": target,
            "readiness_track": "balance",
        }, now_fn=now_fn)
    readiness_mod.spend_balance(
        character, profile["balance_cost"], now_fn=now_fn,
    )
    open_telegraph(
        character, target, verb,
        now_fn=now_fn,
        zone=None,
        profile=profile,
    )
    return True, None


def is_flying(character):
    """True when ``character`` is airborne (overland / aerial flag)."""
    return bool(getattr(character, "is_flying", False))


def open_telegraph(attacker, defender, verb, *, now_fn=None,
                   window_seconds=None, zone=None, profile=None):
    """Create an unresolved Telegraph on ``defender`` and return it.

    Each incoming strike gets its own telegraph (decision #9).
    """
    ensure_defaults(defender)
    profile = profile or VERB_PROFILES.get(verb, VERB_PROFILES["punch"])
    telegraph_id = f"tg_{next(_telegraph_ids)}"
    telegraph = {
        "id": telegraph_id,
        "attacker": attacker,
        "defender": defender,
        "verb": verb,
        "opened_at": _now(now_fn),
        "window_seconds": float(
            window_seconds if window_seconds is not None
            else DEFAULT_TELEGRAPH_WINDOW
        ),
        "resolved": False,
        "resolved_by": None,
        "zone": zone,
        "requires_ground": bool(profile.get("requires_ground")),
        "base_damage": float(profile.get("damage", 8.0)),
        "status": profile.get("status"),
        "balance_cost": float(profile.get("balance_cost", 1.8)),
    }
    getattr(defender, TELEGRAPHS_ATTR)[telegraph_id] = telegraph
    return telegraph


def match_telegraph(defender, *, attacker_name=None):
    """Pick which open telegraph a manual defense answers (§10.3).

    Bare defense -> oldest still-unresolved telegraph on this defender.
    ``attacker_name`` filters by attacker key/name (case-insensitive prefix).
    """
    ensure_defaults(defender)
    open_map = getattr(defender, TELEGRAPHS_ATTR) or {}
    unresolved = [
        tg for tg in open_map.values()
        if not tg.get("resolved")
    ]
    if not unresolved:
        return None
    unresolved.sort(key=lambda tg: float(tg.get("opened_at") or 0.0))
    if attacker_name:
        needle = str(attacker_name).strip().lower()
        for tg in unresolved:
            if _name(tg["attacker"]).lower().startswith(needle):
                return tg
        return None
    return unresolved[0]


def telegraph_window_open(telegraph, *, now_fn=None):
    """True while the defender can still manually answer this telegraph."""
    opened = float(telegraph.get("opened_at") or 0.0)
    window = float(telegraph.get("window_seconds") or DEFAULT_TELEGRAPH_WINDOW)
    return _now(now_fn) < opened + window


def telegraph_window_elapsed(telegraph, *, now_fn=None):
    """True when auto-defense may fire for this telegraph (§10.4)."""
    opened = float(telegraph.get("opened_at") or 0.0)
    window = float(telegraph.get("window_seconds") or DEFAULT_TELEGRAPH_WINDOW)
    return _now(now_fn) >= opened + window


# --- Kinetic default engine (brief / apply / narrate) ----------------------

def _pow(character):
    stats = getattr(character, "stats", None) or {}
    try:
        return float(stats.get("POW", 5.0))
    except (TypeError, ValueError):
        return 5.0


def kinetic_build_brief(attacker, defender, game=None, *, rng=None,
                        telegraph=None, defense_kind=None, manual=False,
                        now_fn=None, **_ctx):
    """Pure math for one telegraph resolution -- no HP mutation.

    Resolve-time posture check (§10.8): ground-only verbs fail vs flying
    defender unless the attacker is also flying.
    """
    telegraph = telegraph or {}
    verb = telegraph.get("verb") or "punch"
    requires_ground = bool(telegraph.get("requires_ground"))
    outcome_tags = []

    if requires_ground and is_flying(defender) and not is_flying(attacker):
        return {
            "engine": ENGINE_ID_DEFAULT,
            "attacker": attacker,
            "defender": defender,
            "verb": verb,
            "outcome": "airborne_miss",
            "defense_kind": None,
            "defense_success": False,
            "damage": 0.0,
            "zone": telegraph.get("zone"),
            "outcome_tags": ["target is airborne"],
            "telegraph_id": telegraph.get("id"),
            "manual": bool(manual),
        }

    base = float(telegraph.get("base_damage") or 8.0)
    # POW scales kinetic output gently around the default-5 band.
    damage = base * (1.0 + 0.06 * (_pow(attacker) - 5.0))
    zone = telegraph.get("zone")
    if zone == "head":
        damage *= 1.35
        outcome_tags.append("aimed_head")
    elif zone:
        damage *= 1.15
        outcome_tags.append(f"aimed_{zone}")

    defense_success = False
    if defense_kind:
        defense_success = defense_mod.roll_defense_success(
            defender, defense_kind, manual=manual, rng=rng,
        )
        defense_mod.bump_proficiency(defender, defense_kind)

    if defense_kind == defense_mod.DEFENSE_DODGE and defense_success:
        damage = 0.0
        outcome = "dodged"
        outcome_tags.append("DODGED")
    elif defense_kind == defense_mod.DEFENSE_BLOCK and defense_success:
        mit = defense_mod.block_mitigation(defender)
        damage = max(0.0, damage - mit)
        outcome = "blocked"
        outcome_tags.append("BLOCKED")
        if damage <= 0:
            outcome_tags.append("FULL_MITIGATION")
        else:
            outcome_tags.append("PARTIAL_MITIGATION")
    elif defense_kind == defense_mod.DEFENSE_PARRY:
        if defense_success:
            damage = 0.0
            outcome = "parried"
            outcome_tags.append("PARRIED")
            # Parry strips the attacker's Balance (doc risk/reward).
            outcome_tags.append("BALANCE_STRIP")
        else:
            damage *= 1.5
            outcome = "parry_fail"
            outcome_tags.append("CRITICAL")
            outcome_tags.append("PARRY_FAIL")
    else:
        outcome = "hit"
        outcome_tags.append("HIT")
        if telegraph.get("status"):
            outcome_tags.append(str(telegraph["status"]).upper())

    return {
        "engine": ENGINE_ID_DEFAULT,
        "attacker": attacker,
        "defender": defender,
        "verb": verb,
        "outcome": outcome,
        "defense_kind": defense_kind,
        "defense_success": defense_success,
        "damage": max(0.0, damage),
        "zone": zone,
        "outcome_tags": outcome_tags,
        "telegraph_id": telegraph.get("id"),
        "manual": bool(manual),
        "status": telegraph.get("status") if outcome == "hit" else None,
    }


def kinetic_apply_brief(brief, game=None):
    """Mutate HP (and optional parry Balance strip) from a frozen brief."""
    from engine.systems import grapple as grapple_mod

    defender = brief["defender"]
    attacker = brief["attacker"]
    damage = float(brief.get("damage") or 0.0)
    if damage:
        agg_damage = damage
        zone = brief.get("zone")
        outcome = brief.get("outcome")
        if zone and outcome in ("hit", "blocked", "parry_fail"):
            from engine.systems import body_parts as body_parts_mod

            agg_damage = float(
                body_parts_mod.apply_incoming_damage(
                    defender,
                    int(damage),
                    zone,
                    limb_actor_check=lambda c: not getattr(c, "is_npc", False),
                )
            )
        current = float(getattr(defender, "hp", 0.0) or 0.0)
        defender.hp = max(0.0, current - agg_damage)
        damage = agg_damage
    if brief.get("verb") == "grab" and brief.get("outcome") == "hit":
        grapple_mod.apply_hold(attacker, defender)
    if "BALANCE_STRIP" in (brief.get("outcome_tags") or []):
        # Force the attacker off Balance immediately (parry payoff).
        readiness_mod.spend_balance(attacker, 2.5)
    return {
        "outcome": brief.get("outcome"),
        "damage": damage,
        "defense_kind": brief.get("defense_kind"),
    }


def kinetic_narrate(brief, result):
    """Tag-first one-liner from brief + result (hard rule 5 / decision #7)."""
    atk = _name(brief["attacker"])
    dfn = _name(brief["defender"])
    verb = brief.get("verb") or "strike"
    outcome = brief.get("outcome")
    damage = float(result.get("damage") or 0.0)
    zone = brief.get("zone")
    zone_bit = f" ({zone})" if zone else ""

    if outcome == "airborne_miss":
        return f"[MISS] {atk}'s {verb} can't reach airborne {dfn}."
    if outcome == "hit" and verb == "grab":
        return f"[GRAB] {atk} seizes {dfn}."
    if outcome == "dodged":
        return f"[DODGED] {dfn} dodges {atk}'s {verb}{zone_bit}."
    if outcome == "blocked":
        return (
            f"[BLOCKED] {dfn} blocks {atk}'s {verb}{zone_bit} "
            f"({damage:.0f} through)."
        )
    if outcome == "parried":
        return f"[PARRIED] {dfn} parries {atk}'s {verb}{zone_bit}."
    if outcome == "parry_fail":
        return (
            f"[CRITICAL] {dfn}'s failed parry leaves them open -- "
            f"{atk}'s {verb}{zone_bit} lands for {damage:.0f}."
        )
    return f"[HIT] {atk}'s {verb}{zone_bit} hits {dfn} for {damage:.0f}."


def compress_narrate(briefs_and_results):
    """Compress one tick's (brief, result) list for a pair into one string.

    Caller already groups by (attacker, defender). Multiple hits become a
    single line listing tags + total damage.
    """
    if not briefs_and_results:
        return None
    if len(briefs_and_results) == 1:
        brief, result = briefs_and_results[0]
        narrate_fn = get_active_combat_engine(
            brief.get("engine") or ENGINE_ID_DEFAULT
        )
        if narrate_fn and narrate_fn.get("narrate"):
            return narrate_fn["narrate"](brief, result)
        return kinetic_narrate(brief, result)

    brief0 = briefs_and_results[0][0]
    atk = _name(brief0["attacker"])
    dfn = _name(brief0["defender"])
    tags = []
    total = 0.0
    for brief, result in briefs_and_results:
        for tag in brief.get("outcome_tags") or []:
            if tag not in tags and tag.isupper():
                tags.append(tag)
        total += float(result.get("damage") or 0.0)
    tag_prefix = " ".join(f"[{t}]" for t in tags) or "[HIT]"
    n = len(briefs_and_results)
    return (
        f"{tag_prefix} {atk} lands a {n}-hit exchange on {dfn} "
        f"({total:.0f} total)."
    )


# Self-register the kinetic default on import.
register_active_combat_engine(
    ENGINE_ID_DEFAULT,
    build_brief=kinetic_build_brief,
    apply_brief=kinetic_apply_brief,
    narrate=kinetic_narrate,
)


# --- Resolve helpers -------------------------------------------------------

def _resolve_telegraph(telegraph, *, defense_kind, manual, game=None,
                       rng=None, now_fn=None, engine_id=None):
    """Mark telegraph resolved, run build/apply, return (brief, result)."""
    if telegraph.get("resolved"):
        return None, None
    engine_id = engine_id or ENGINE_ID_DEFAULT
    engine = get_active_combat_engine(engine_id) or get_active_combat_engine(
        ENGINE_ID_DEFAULT
    )
    brief = engine["build_brief"](
        telegraph["attacker"],
        telegraph["defender"],
        game,
        rng=rng,
        telegraph=telegraph,
        defense_kind=defense_kind,
        manual=manual,
        now_fn=now_fn,
    )
    result = engine["apply_brief"](brief, game)
    telegraph["resolved"] = True
    telegraph["resolved_by"] = "manual" if manual else (
        "auto" if defense_kind else "none"
    )
    # Drop resolved telegraphs from the defender's map so the dict stays
    # bounded across long fights.
    open_map = getattr(telegraph["defender"], TELEGRAPHS_ATTR, None)
    if isinstance(open_map, dict):
        open_map.pop(telegraph.get("id"), None)
    return brief, result


def _head_ready(character, *, now_fn=None):
    """Return the head-of-queue command if it can resolve this tick, else None.

    Offense/aim wait on readiness. Manual defense is readiness-free (§10.1)
    but is dropped later if its window already closed. clear_queue is always
    ready.
    """
    queue = get_queue(character)
    if not queue:
        return None
    cmd = queue[0]
    kind = cmd.get("kind")
    if kind == _KIND_CLEAR:
        return cmd
    if kind == _KIND_MANUAL_DEFENSE:
        return cmd
    track = cmd.get("readiness_track")
    if track and not readiness_mod.is_ready(character, track, now_fn=now_fn):
        return None
    return cmd


def _resolve_command(character, cmd, *, game=None, rng=None, now_fn=None,
                     resolved_bucket=None):
    """Resolve one queued command. Mutates queue (caller pops after success).

    Returns True if the command was consumed (resolved or dropped), False
    if it must stay at the head (not used -- readiness already filtered).
    """
    kind = cmd.get("kind")
    queue = get_queue(character)

    if kind == _KIND_CLEAR:
        # Decision #20: clear remaining pending actions, not open telegraphs.
        # The clear command itself is about to be popped by the caller, so
        # clear everything currently in the queue including it.
        clear_queue(character)
        return True

    if kind == _KIND_AIM:
        from engine.systems import firearms as firearms_mod
        target = cmd.get("target")
        zone = cmd.get("zone")
        if target is None:
            return True
        ok, _msg = firearms_mod.set_sight(character, target, zone=zone)
        if ok:
            readiness_mod.spend_equilibrium(
                character, AIM_EQUILIBRIUM_COST, now_fn=now_fn,
            )
        return True

    if kind == _KIND_LOAD:
        from engine.systems import firearms as firearms_mod
        ok, _msg = firearms_mod.load_chamber(character)
        if ok:
            readiness_mod.spend_balance(
                character, LOAD_BALANCE_COST, now_fn=now_fn,
            )
        return True

    if kind == _KIND_FIRE:
        from engine.systems import firearms as firearms_mod
        if not firearms_mod.can_fire(character):
            return True
        sight = firearms_mod.get_sight(character) or {}
        target = sight.get("target")
        zone = sight.get("zone")
        if target is None:
            return True
        profile = VERB_PROFILES["fire"]
        readiness_mod.spend_balance(
            character, profile["balance_cost"], now_fn=now_fn,
        )
        firearms_mod.consume_chamber(character)
        open_telegraph(
            character, target, "fire",
            now_fn=now_fn,
            zone=zone,
            profile=profile,
        )
        return True

    if kind == _KIND_MANUAL_DEFENSE:
        defense_kind = cmd.get("defense_kind")
        telegraph = match_telegraph(
            character, attacker_name=cmd.get("attacker_name"),
        )
        if telegraph is None:
            return True  # nothing to answer -- consume the wasted defense
        # Missed window -> drop, do not resolve late (§8 smoke / §10.4).
        if not telegraph_window_open(telegraph, now_fn=now_fn):
            return True
        # Manual defense typed during window -- does NOT spend Balance.
        brief, result = _resolve_telegraph(
            telegraph,
            defense_kind=defense_kind,
            manual=True,
            game=game,
            rng=rng,
            now_fn=now_fn,
        )
        if brief is not None and resolved_bucket is not None:
            resolved_bucket.append({
                "brief": brief,
                "result": result,
                "timestamp": float(cmd.get("timestamp") or _now(now_fn)),
            })
        return True

    if kind == _KIND_OFFENSE:
        target = cmd.get("target")
        verb = cmd.get("verb") or "punch"
        profile = VERB_PROFILES.get(verb, VERB_PROFILES["punch"])
        if target is None:
            return True
        # Spend Balance at resolve time, then open a telegraph -- damage
        # waits for defense window / auto-defense.
        readiness_mod.spend_balance(
            character, profile["balance_cost"], now_fn=now_fn,
        )
        open_telegraph(
            character, target, verb,
            now_fn=now_fn,
            zone=None,
            profile=profile,
        )
        return True

    # Unknown kind -- drop to avoid wedging the queue.
    return True


def _auto_defense_sweep(game, *, now_fn=None, rng=None, resolved_bucket=None):
    """Resolve every elapsed unresolved telegraph via auto-defense (§10.4).

    Never skips Echoes (§10.7) -- no ``session is None`` gate.
    """
    from engine.char_index import iter_characters

    for character in list(iter_characters(game)):
        ensure_defaults(character)
        open_map = getattr(character, TELEGRAPHS_ATTR) or {}
        # Snapshot values -- resolve mutates the dict.
        for telegraph in list(open_map.values()):
            if telegraph.get("resolved"):
                continue
            if not telegraph_window_elapsed(telegraph, now_fn=now_fn):
                continue
            kind = defense_mod.choose_auto_defense(
                character, telegraph.get("verb") or "punch", rng=rng,
            )
            brief, result = _resolve_telegraph(
                telegraph,
                defense_kind=kind,
                manual=False,
                game=game,
                rng=rng,
                now_fn=now_fn,
            )
            if brief is not None and resolved_bucket is not None:
                resolved_bucket.append({
                    "brief": brief,
                    "result": result,
                    # Use window-end as the causal timestamp for ordering.
                    "timestamp": (
                        float(telegraph.get("opened_at") or 0.0)
                        + float(telegraph.get("window_seconds")
                                or DEFAULT_TELEGRAPH_WINDOW)
                    ),
                })


def _compress_and_narrate(resolved_bucket):
    """Group by (attacker, defender), order by last resolve time (§10.9)."""
    if not resolved_bucket:
        return []
    groups = {}
    order_key = {}
    for entry in resolved_bucket:
        brief = entry["brief"]
        pair = (id(brief["attacker"]), id(brief["defender"]))
        groups.setdefault(pair, []).append((brief, entry["result"]))
        ts = float(entry.get("timestamp") or 0.0)
        order_key[pair] = max(order_key.get(pair, 0.0), ts)
    lines = []
    for pair in sorted(groups.keys(), key=lambda p: order_key[p]):
        text = compress_narrate(groups[pair])
        if text:
            lines.append({
                "text": text,
                "attacker": groups[pair][0][0]["attacker"],
                "defender": groups[pair][0][0]["defender"],
            })
    return lines


def tick_active_combat(game, *, now_fn=None, rng=None):
    """Heartbeat entry: drain queues globally, auto-defend, compress prose.

    Returns a list of ``{"text", "attacker", "defender"}`` narration lines
    for the caller (basegame demo) to broadcast. Pure engine -- no Session
    writes here.
    """
    from engine.char_index import iter_characters

    resolved_bucket = []

    # §10.2: repeatedly pick the globally earliest ready head-of-queue.
    # Cap iterations so a bug cannot spin forever; N*cap is plenty.
    characters = list(iter_characters(game))
    max_steps = max(1, len(characters) * (OFFENSE_QUEUE_CAP + 2) + 8)
    for _ in range(max_steps):
        ready = []
        for character in characters:
            cmd = _head_ready(character, now_fn=now_fn)
            if cmd is not None:
                ready.append((float(cmd.get("timestamp") or 0.0),
                              character, cmd))
        if not ready:
            break
        ready.sort(key=lambda row: row[0])
        _ts, character, cmd = ready[0]
        queue = get_queue(character)
        # clear_queue empties the whole queue including this command.
        if cmd.get("kind") == _KIND_CLEAR:
            _resolve_command(
                character, cmd, game=game, rng=rng, now_fn=now_fn,
                resolved_bucket=resolved_bucket,
            )
            continue
        _resolve_command(
            character, cmd, game=game, rng=rng, now_fn=now_fn,
            resolved_bucket=resolved_bucket,
        )
        if queue and queue[0] is cmd:
            queue.pop(0)
        elif queue and cmd in queue:
            queue.remove(cmd)

    # Manual defenses already drained above; auto only after window elapses.
    _auto_defense_sweep(
        game, now_fn=now_fn, rng=rng, resolved_bucket=resolved_bucket,
    )
    return _compress_and_narrate(resolved_bucket)


def on_disconnect_clear_offense(character):
    """Hard rule 8 / §10.7: drop queued offense silently; keep telegraphs.

    Open telegraphs against this Echo still auto-resolve when their windows
    elapse -- never skip auto-defense for session-less bodies.
    """
    clear_queue(character)
