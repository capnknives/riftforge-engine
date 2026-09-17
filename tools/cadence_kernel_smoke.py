"""cadence_kernel_smoke.py -- Wave 4 Cadence seek/wander kernel gate.

Stdlib + engine + basegame only -- never imports supers.
Usage: python3.13 tools/cadence_kernel_smoke.py
"""

from __future__ import annotations

import json
import os
import re
import sys


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_repo_on_path():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)


def _ok(msg):
    print(f"ok: {msg}")


def _fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    _ensure_repo_on_path()
    os.environ["RIFTFORGE_GAME"] = "basegame"

    from engine import hooks
    from engine.gmcp import OCCUPANT_KIND_NPC, occupants_payload
    from engine.systems import cadence_kernel as ck
    from engine.systems import needs as needs_engine
    from world import Character, Room

    # 1. Hunger wins when above threshold.
    hungry = Character("SmokeHungry")
    hungry.hunger = 0.7
    hungry.thirst = 0.1
    assert ck.most_urgent_need(hungry) == "hunger", "assert 1 hunger pick"
    _ok("most_urgent_need picks hunger at 0.7")

    # 2. Content when both meters are under threshold.
    fine = Character("SmokeFine")
    fine.hunger = 0.2
    fine.thirst = 0.3
    assert ck.most_urgent_need(fine) is None, "assert 2 under threshold"
    _ok("most_urgent_need None under SEEK")

    # 3. pick_seek_room + plan_tick cardinal toward food room.
    zone = "smoke_zone"
    start = Room("start")
    start.zone = zone
    dest = Room("store")
    dest.zone = zone
    dest.resources = ("food",)
    start.exits = {"east": dest}
    dest.exits = {"west": start}
    actor = Character("SmokeSeeker")
    actor.zone = zone
    actor.hunger = 0.65
    actor.thirst = 0.1
    actor.location = start
    start.characters = lambda: [actor]
    game = type("G", (), {"rooms": {"start": start, "store": dest}})()

    picked = ck.pick_seek_room(actor, game)
    assert picked is dest, "assert 3 pick_seek_room dest"
    plan = ck.plan_tick(actor, game)
    assert isinstance(plan, dict), "plan_tick returns dict"
    assert plan.get("verb") == "east", f"assert 3 seek hop verb, got {plan!r}"
    assert "seek" in str(plan.get("reason")), f"assert 3 seek reason, got {plan!r}"
    _ok("pick_seek_room + plan_tick seek hop east")

    # 4. Sate in food room zeros hunger without npc_do.
    actor.location = dest
    actor.hunger = 0.65
    plan_sate = ck.plan_tick(actor, game)
    assert plan_sate.get("verb") is None, "assert 4 sate verb None"
    assert str(plan_sate.get("reason", "")).startswith("sate:hunger"), plan_sate
    ck.apply_plan(actor, game, plan_sate)
    assert abs(actor.hunger) < 1e-9, "assert 4 hunger zeroed"
    _ok("sate:hunger zeros hunger via apply_plan")

    # 5. plan_tick never mutates location.
    wander_room = Room("w")
    wander_room.zone = zone
    neighbor = Room("n")
    neighbor.zone = zone
    wander_room.exits = {"north": neighbor}
    neighbor.exits = {"south": wander_room}
    actor.location = wander_room
    actor.hunger = 0.1
    actor.thirst = 0.1
    loc_id = id(actor.location)
    _ = ck.plan_tick(actor, game)
    assert id(actor.location) == loc_id, "assert 5 plan_tick no mutate"
    _ok("plan_tick does not mutate location")

    # 6. Occupants payload shows NPC kind without VNUM leak.
    viewer = Character("SmokeViewer")
    viewer.location = wander_room
    npc = Character("SmokeNpc")
    npc.is_npc = True
    npc.location = wander_room
    wander_room.characters = lambda: [viewer, npc]
    payload = occupants_payload(viewer)
    assert isinstance(payload, dict), "occupants_payload dict"
    who = payload.get("who") or []
    npc_rows = [row for row in who if row.get("kind") == OCCUPANT_KIND_NPC]
    assert npc_rows, "assert 6 NPC row present"
    assert npc_rows[0].get("token") == "person", npc_rows[0]
    blob = json.dumps(payload)
    assert not re.search(r"[A-Z]{2}\d{5}", blob), "assert 6 no VNUM in JSON"
    _ok("Occupants NPC kind person, no VNUM leak")

    # 7. Purity: touched engine files must not import supers.
    import re as _re
    touched = (
        "engine/systems/cadence_kernel.py",
        "engine/hooks.py",
    )
    import_pat = _re.compile(r"^\s*(from|import)\s+supers\b", _re.MULTILINE)
    for rel in touched:
        path = os.path.join(_repo_root(), rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if import_pat.search(text):
            _fail(f"assert 7 supers import in {rel}")
    _ok("engine kernel + hooks stay supers-free")

    # 8. apply_plan dispatches cardinal via hooks.set_dispatch / npc_do path.
    dispatch_log = []

    def _record_dispatch(character, raw, game_obj):
        dispatch_log.append(str(raw))
        return True

    hooks.set_dispatch(_record_dispatch)
    hop_actor = Character("SmokeDispatch")
    hop_actor.location = start
    hop_actor.hunger = 0.65
    hop_plan = {"verb": "east", "reason": "seek:hunger"}
    ck.apply_plan(hop_actor, game, hop_plan)
    assert dispatch_log == ["east"], f"assert 8 dispatch, got {dispatch_log!r}"
    _ok("apply_plan records east through dispatch hook")

    print("cadence_kernel_smoke: ok")


if __name__ == "__main__":
    main()
