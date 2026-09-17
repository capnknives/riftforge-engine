"""Targeted smoke: folklore Wave 5a grace tank + Wave 5b vessel chassis peel.

Stdlib + engine (+ basegame catalogs). NEVER import supers.

Run:
  python3.13 tools/grace_vessel_smoke.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_VNUM_RE = re.compile(r"[A-Z]{2}\d{5}")


def _assert(cond, msg):
    if not cond:
        raise SystemExit(f"grace_vessel_smoke: {msg}")


def test_grace_math():
    from engine.systems.grace import (
        GRACE_MAX,
        add_grace_amount,
        clamp_grace,
        grace_drip_amount,
    )

    _assert(GRACE_MAX == 100.0, f"GRACE_MAX expected 100.0, got {GRACE_MAX}")
    _assert(clamp_grace(-5.0) == 0.0, "negative should floor at 0")
    _assert(clamp_grace(150.0) == 100.0, "overflow should clamp to cap")
    _assert(clamp_grace(50.556) == 50.56, "should round to two decimals")

    new_val, delta = add_grace_amount(80.0, 30.0)
    _assert(new_val == 100.0 and delta == 20.0, (new_val, delta))
    new_val, delta = add_grace_amount(10.0, -25.0)
    _assert(new_val == 0.0 and delta == -10.0, (new_val, delta))

    _assert(grace_drip_amount(on_home_plane=True, on_consecrated=False) == 0.20, "home drip")
    _assert(grace_drip_amount(on_home_plane=False, on_consecrated=True) == 0.05, "consecrated drip")
    _assert(grace_drip_amount(on_home_plane=False, on_consecrated=False) == 0.0, "secular drip")


def test_vessel_chassis():
    from engine.systems import vessel as vessel_mod

    rider = types.SimpleNamespace(key="MantleDemo", location=None)
    host = types.SimpleNamespace(
        key="husk:HostBody",
        given_name="Jimmy",
        surname="Novak",
        location=None,
    )
    room = types.SimpleNamespace(
        _chars=[rider, host],
        characters=lambda: room._chars,
    )
    rider.location = room
    host.location = room

    vessel_mod.ensure_vessel_defaults(rider)
    _assert(not vessel_mod.is_riding(rider), "fresh mantle should not ride")
    _assert(not vessel_mod.is_possessed_host(host), "fresh host not possessed")

    vessel_mod.ensure_vessel_defaults(rider)
    vessel_mod.ensure_vessel_defaults(host)
    _assert(hasattr(rider, "vessel_free"), "defaults should stamp vessel_free")

    rider.vessel_host_key = host.key
    host.possessed_by = rider.key
    _assert(vessel_mod.is_riding(rider), "rider should be riding")
    _assert(vessel_mod.is_possessed_host(host), "host should be possessed")

    actor = vessel_mod.public_actor(rider, game=None)
    _assert(actor is host, "public_actor should return host while riding")

    # Cross-room: Game.find_character is a name resolver; chassis must use
    # exact key lookup so husk: keys still resolve.
    other = types.SimpleNamespace(key="FarHost", given_name="Pat", surname="Lee")
    vessel_mod.ensure_vessel_defaults(other)
    far_rider = types.SimpleNamespace(key="FarMantle", location=None)
    vessel_mod.ensure_vessel_defaults(far_rider)
    far_rider.vessel_host_key = other.key
    stub_game = types.SimpleNamespace(
        characters={far_rider.key: far_rider, other.key: other},
        rooms={},
    )
    far_actor = vessel_mod.public_actor(far_rider, game=stub_game)
    _assert(far_actor is other, "public_actor must find host by exact key")

    name = vessel_mod.public_name(rider, game=None)
    _assert(isinstance(name, str), "public_name must return str")
    for bad in ("husk:", "plot:", "RK00001"):
        _assert(bad not in name, f"public_name leaked {bad!r}: {name!r}")


def test_occupant_tokens():
    from engine import gmcp
    from engine import hooks
    from engine.systems import vessel as vessel_mod

    bystander = types.SimpleNamespace(key="Walker", location=None)
    rider = types.SimpleNamespace(key="Rider", location=None, vessel_host_key=None)
    host = types.SimpleNamespace(
        key="Host",
        location=None,
        possessed_by=None,
    )
    room = types.SimpleNamespace(
        _chars=[bystander, rider, host],
        characters=lambda: room._chars,
    )
    for obj in room._chars:
        obj.location = room

    vessel_mod.ensure_vessel_defaults(rider)
    vessel_mod.ensure_vessel_defaults(host)
    rider.vessel_host_key = host.key
    host.possessed_by = rider.key

    def _occupant_token(obj, viewer):
        del viewer
        if vessel_mod.is_riding(obj) or vessel_mod.is_possessed_host(obj):
            return "vessel"
        return None

    hooks.set_occupant_token(_occupant_token)
    viewer = types.SimpleNamespace(key="Viewer", location=room)
    payload = gmcp.occupants_payload(viewer)
    _assert(isinstance(payload, dict), "occupants_payload must return dict")
    who = {row["name"]: row["token"] for row in payload["who"]}
    _assert(who.get("Walker") == "person", who)
    _assert(who.get("Rider") == "vessel", who)
    dumped = json.dumps(payload)
    _assert(_VNUM_RE.search(dumped) is None, f"VNUM leaked in occupants JSON: {dumped}")


def test_fake_vitals_grace_keys():
    from engine import hooks
    from engine.systems.grace import GRACE_MAX

    def _fake_vitals(_character):
        return {
            "hp": "100",
            "maxhp": "100",
            "grace": "100",
            "maxgrace": str(int(GRACE_MAX)),
        }

    hooks.set_gmcp_char_vitals(_fake_vitals)
    demo = types.SimpleNamespace(key="DemoCelestial", grace=100.0)
    vitals = hooks.gmcp_char_vitals(demo)
    _assert(isinstance(vitals, dict), "vitals hook must return dict")
    _assert(vitals.get("grace") == "100", vitals)
    _assert(vitals.get("maxgrace") == "100", vitals)
    dumped = json.dumps(vitals)
    _assert("Castiel" not in dumped, "vitals must not hardcode Castiel")


def main():
    test_grace_math()
    test_vessel_chassis()
    test_occupant_tokens()
    test_fake_vitals_grace_keys()
    print("grace_vessel_smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
