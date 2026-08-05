"""vehicles.py -- basegame demo vehicle catalog registration.

Registers the engine vehicle catalog loader for basegame/content/vehicles.json
and ensures catalog vehicles at boot (see basegame/bootstrap.py).
"""

from __future__ import annotations

import json
import os

from engine import hooks
from engine.systems import vehicles as vehicles_mod

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")


def _load_vehicles_json():
    """Return the basegame vehicle catalog dict."""
    path = os.path.join(_CONTENT_DIR, "vehicles.json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def register_vehicle_hooks():
    """Wire engine vehicle catalog hooks for the basegame demo."""
    hooks.register_vehicle_catalog(_load_vehicles_json)
    hooks.register_travel_hub_catalog(lambda: {})


def ensure_basegame_vehicles(game):
    """Stamp catalog vehicles onto ``game`` (idempotent)."""
    vehicles_mod.ensure_game_vehicles(game)
