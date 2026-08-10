"""
town_spine.py -- parameterized civic spine generator for townforge.

Extracts the Lebanon/Lawrence grid patterns into a TownSpec-driven builder
that writes zone JSON room graphs (commercial fixtures, amenities, sewers,
neighborhood stubs). Retail/civic storefronts use the player-shop fixture
model (commercial curb + pocket hub + player_shop_fixtures manifest).

Design SoT: docs/plans/townforge.md
"""

from __future__ import annotations

from engine.systems import town_roads

OPPOSITE = town_roads.OPPOSITE

# Game hooks (SUPERS townforge + player_shops register at bootstrap).
_CIVIC_OWNER_PREFIX = "@civic:"
_townforge_normalize_spec = None
_townforge_size_params = None
_townforge_region_style = None
_townforge_load_amenity_recipes = None
_townforge_amenities_for_spec = None
_townforge_resolve_spine_template = None


def set_civic_owner_prefix(prefix):
    global _CIVIC_OWNER_PREFIX
    _CIVIC_OWNER_PREFIX = str(prefix)


def civic_owner_prefix():
    return _CIVIC_OWNER_PREFIX


def set_townforge_normalize_spec(fn):
    global _townforge_normalize_spec
    _townforge_normalize_spec = fn


def set_townforge_size_params(fn):
    global _townforge_size_params
    _townforge_size_params = fn


def set_townforge_region_style(fn):
    global _townforge_region_style
    _townforge_region_style = fn


def set_townforge_load_amenity_recipes(fn):
    global _townforge_load_amenity_recipes
    _townforge_load_amenity_recipes = fn


def set_townforge_amenities_for_spec(fn):
    global _townforge_amenities_for_spec
    _townforge_amenities_for_spec = fn


def set_townforge_resolve_spine_template(fn):
    global _townforge_resolve_spine_template
    _townforge_resolve_spine_template = fn


def _require_townforge_hooks():
    if _townforge_normalize_spec is None:
        raise RuntimeError("townforge spec hooks not registered")


class NameCounters:
    """Allocate unowned shopN / unowned amenityN keys in order."""

    def __init__(self):
        self.shop = 0
        self.amenity = 0

    def next_shop(self):
        self.shop += 1
        return f"unowned shop{self.shop}"

    def next_amenity(self):
        self.amenity += 1
        return f"unowned amenity{self.amenity}"


def _room(key, desc, zone_id, x, y, z=0, city_name="", **kw):
    """Build one room dict with Studio layout."""
    area_type = kw.pop("area_type", "city_street")
    title = kw.pop("title", None)
    r = {
        "key": key,
        "description": desc,
        "area_type": area_type,
        "zone": zone_id,
        "wilderness": False,
        "exits": {},
        "layout": {"x": int(x), "y": int(y), "z": int(z)},
        "city_name": city_name,
    }
    if title:
        r["title"] = title
    for flag in (
        "outdoor", "is_house", "is_home", "private_home", "hospital", "is_grave",
        "no_combat", "dark", "evil_zone", "resources", "jobs", "seed_items",
        "resource_capacity", "robable", "main_homeroom", "shop_amenity",
        "shop_tags", "spawn_nest", "zoning", "player_shop_hub",
    ):
        if flag in kw and kw[flag] is not None:
            r[flag] = kw[flag]
    return r


def _dig(rooms, a_key, direction, b_key):
    rooms[a_key]["exits"][direction] = b_key
    back = OPPOSITE.get(direction)
    if back:
        rooms[b_key]["exits"][back] = a_key


def _add(rooms, room):
    key = room["key"]
    if key in rooms:
        raise ValueError(f"duplicate room key {key!r}")
    rooms[key] = room


def _sewer_plus(rooms, hub_key, hub_x, hub_y, arms, *, zone_id, city_name):
    """Build sewer + under a surface hub (Lebanon pattern)."""
    sk = f"{hub_key} Sewer"
    _add(rooms, _room(
        sk,
        "A brick junction under the street — tunnels run the cardinals. "
        "A rusted ladder leads up to daylight.",
        zone_id, hub_x, hub_y, z=-1,
        city_name=city_name,
        dark=True, outdoor=False, evil_zone=True, area_type="ruins",
    ))
    _dig(rooms, hub_key, "down", sk)
    for direction, length in arms.items():
        prev = sk
        dx, dy = {
            "north": (0, 1), "south": (0, -1),
            "east": (1, 0), "west": (-1, 0),
        }[direction]
        for i in range(1, int(length) + 1):
            key = f"{sk} {direction.title()} {i}"
            _add(rooms, _room(
                key,
                f"A {direction}bound sewer tunnel under the town.",
                zone_id, hub_x + dx * i, hub_y + dy * i, z=-1,
                city_name=city_name,
                dark=True, outdoor=False, evil_zone=True, area_type="ruins",
                # Segment index in title — boot heal_duplicate_hand_room_titles
                # collapses rooms that share a ROOM NAME and rewires exits,
                # which turns sewer chains into self-loops (DT00046 west → DT00046).
                title=_title(city_name, f"Sewer {direction.title()}", str(i)),
            ))
            _dig(rooms, prev, direction, key)
            prev = key
    return sk


def _recipe_uses_player_shop(recipe):
    """True when amenity should be a street fixture + pocket hub."""
    if not recipe:
        return False
    return bool(recipe.get("shop_amenity"))


def _stamp_player_shop_fixture(
    rooms,
    spec,
    amenity_id,
    recipe,
    street_key,
    zone_id,
    city,
    fixture_manifest,
):
    """Stamp commercial host + pocket hub; append fixture manifest row."""
    amenity_type = str(recipe.get("shop_amenity") or amenity_id).strip().lower()
    slug = spec["slug"]
    shop_id = f"pshop:civic:{slug}-{amenity_id}"
    hub_key = f"pshop-{slug}-{amenity_id}"
    enter_alias = amenity_id.replace("_", "-")[:24]
    display = recipe.get("display_name") or (
        f"{city} {amenity_id.replace('_', ' ').title()}"
    )
    street = rooms[street_key]
    street["zoning"] = "commercial"
    layout = street["layout"]
    hx = int(layout["x"]) + 2
    hy = int(layout["y"]) + 2
    extra = {"player_shop_hub": True}
    for field in (
        "resources", "jobs", "robable", "hospital", "resource_capacity",
        "shop_tags", "seed_items",
    ):
        if field in recipe and recipe[field] is not None:
            extra[field] = recipe[field]
    _add(rooms, _room(
        hub_key,
        recipe.get("description") or f"Inside {display}.",
        zone_id, hx, hy, z=-2, city_name=city,
        outdoor=False,
        title=display,
        **extra,
    ))
    fixture_manifest.append({
        "shop_id": shop_id,
        "host_room_key": street_key,
        "hub_room_key": hub_key,
        "enter_alias": enter_alias,
        "display_name": display,
        "amenity_type": amenity_type,
        "owner_key": f"{civic_owner_prefix()}{zone_id}",
    })
    return hub_key


def _add_side_room(
    rooms, names, recipe, x, y, street_key, to_street, zone_id, city_name,
    *,
    amenity_id=None,
):
    """Stamp one civic shop/amenity off a street cell."""
    kind = recipe.get("kind") or "amenity"
    key = names.next_shop() if kind == "shop" else names.next_amenity()
    extra = {}
    for field in (
        "resources", "jobs", "robable", "shop_amenity", "shop_tags",
        "resource_capacity", "hospital", "outdoor", "is_grave", "spawn_nest",
    ):
        if field in recipe and recipe[field] is not None:
            extra[field] = recipe[field]
    if recipe.get("outdoor"):
        extra["area_type"] = "city_street"
        extra["outdoor"] = True
    label = recipe.get("display_name")
    if not label and amenity_id:
        label = str(amenity_id).replace("_", " ").title()
    if label:
        extra["title"] = _title(city_name, label, "Civic")
    _add(rooms, _room(
        key, recipe.get("description") or "An unclaimed civic room.",
        zone_id, x, y, z=0, city_name=city_name, **extra,
    ))
    _dig(rooms, key, to_street, street_key)
    return key


def _link_sewer_surface_grates(rooms, links):
    """Add surface ``down`` grates into sewer arm tips (Lebanon-style).

    *links* is ``[(surface_key, sewer_tip_key), ...]``. Skips missing
    rooms and surfaces that already have ``down``.
    """
    for surface_key, sewer_tip in links:
        surface = rooms.get(surface_key)
        sewer = rooms.get(sewer_tip)
        if surface is None or sewer is None:
            continue
        surface_exits = surface.setdefault("exits", {})
        if surface_exits.get("down"):
            continue
        surface_exits["down"] = sewer_tip
        sewer_exits = sewer.setdefault("exits", {})
        if not sewer_exits.get("up"):
            sewer_exits["up"] = surface_key


def _nest_up(rooms, base_key, nest_kind, zone_id, city_name, recipe):
    """Hotel guest room or clinic ward above an amenity."""
    layout = rooms[base_key]["layout"]
    hx, hy = layout["x"], layout["y"]
    parent_title = (rooms[base_key].get("title") or base_key).strip()
    if nest_kind == "guest_room":
        guest = f"{base_key} Room"
        _add(rooms, _room(
            guest,
            "A clean-enough guest room with blackout curtains and a soft bed.",
            zone_id, hx, hy, z=1, city_name=city_name,
            resources=["sleep", "water", "entertainment", "hygiene"],
            resource_capacity={"sleep": 2},
            seed_items=[{"item": "worn_bed"}],
            title=f"{parent_title} — Guest Room",
        ))
        _dig(rooms, base_key, "up", guest)
    elif nest_kind == "ward":
        ward = f"{base_key} Ward"
        _add(rooms, _room(
            ward,
            "Narrow cots and a medicine cabinet. Down returns to the clinic.",
            zone_id, hx, hy, z=1, city_name=city_name,
            hospital=True,
            resources=["clinic", "sleep"],
            resource_capacity={"sleep": 4},
            title=f"{parent_title} — Ward",
        ))
        _dig(rooms, base_key, "up", ward)


def _title(city_name, main, sub):
    return f"{city_name} - {main} - {sub}"


def build_zone(spec, *, recipes=None, amenity_ids=None):
    """Return a zone document dict for ``spec`` (normalized TownSpec)."""
    _require_townforge_hooks()
    spec = _townforge_normalize_spec(spec)
    city = spec["name"]
    zone_id = spec["zone_id"]
    params = _townforge_size_params(spec)
    style = _townforge_region_style(spec)
    main_len = int(params["main_len"])
    highway_len = int(params["highway_len"])
    neighbor_arm = int(params["neighborhood_arm"])
    recipes = recipes or _townforge_load_amenity_recipes()
    amenity_ids = amenity_ids or _townforge_amenities_for_spec(spec)
    skip = set((spec.get("overrides") or {}).get("skip_amenities") or [])
    extra = list((spec.get("overrides") or {}).get("extra_amenities") or [])
    for item in extra:
        if item not in amenity_ids:
            amenity_ids.append(item)
    amenity_ids = [a for a in amenity_ids if a not in skip and a in recipes]

    rooms = {}
    names = NameCounters()
    fixture_manifest = []
    square_key = f"{city} Square"
    welcome_key = f"{city} Welcome Sign"

    _add(rooms, _room(
        square_key,
        f"The town square — {city} spreads out along Main Street. "
        "A steel manhole cover sits in the pavement.",
        zone_id, 0, 0, z=0, city_name=city,
        outdoor=True, no_combat=True,
        area_type="city_street",
        title=_title(city, "Main Street", "Square"),
        resources=["social", "plaza", "water", "vendor"],
    ))
    _add(rooms, _room(
        welcome_key,
        f"A roadside welcome sign for {city} — north into the square, "
        "south toward the highway edge.",
        zone_id, 0, -1, z=0, city_name=city,
        outdoor=True, area_type="city_street",
        title=_title(city, "Main Street", "Welcome"),
        resources=["social"],
    ))
    _dig(rooms, square_key, "south", welcome_key)

    main_n = []
    prev = square_key
    for i in range(1, main_len + 1):
        key = f"Main Street N{i}"
        _add(rooms, _room(
            key,
            f"Main Street north of the square (block {i}).",
            zone_id, 0, i, z=0, city_name=city,
            outdoor=True, area_type="city_street",
            title=_title(city, "Main Street", f"N{i}"),
        ))
        _dig(rooms, prev, "north", key)
        main_n.append(key)
        prev = key

    main_s = [welcome_key]
    prev = welcome_key
    south_blocks = max(1, main_len - 1)
    entrance_key = None
    for i in range(1, south_blocks + 1):
        key = f"Main Street S{i}"
        is_tip = i == south_blocks
        desc = (
            f"The southern highway into {city} — cracked asphalt and grain dust. "
            "North into town. Type exit to return to the America overland grid."
            if is_tip
            else f"Main Street south of the welcome turnout (block {i})."
        )
        kw = {"outdoor": True, "area_type": "city_street"}
        if is_tip:
            entrance_key = key
            kw["title"] = _title(city, "Southern Highway", "Mouth")
            kw["resources"] = ["social"]
        else:
            kw["title"] = _title(city, "Main Street", f"S{i}")
        _add(rooms, _room(key, desc, zone_id, 0, -(i + 1), z=0, city_name=city, **kw))
        _dig(rooms, prev, "south", key)
        main_s.append(key)
        prev = key

    # Pair amenities onto main blocks (alternate N/S, west/east).
    street_pool = []
    for idx, key in enumerate(main_n[1:], start=2):
        street_pool.append((key, "west" if idx % 2 else "east"))
    for idx, key in enumerate(main_s[1:], start=2):
        street_pool.append((key, "west" if idx % 2 else "east"))
    amen_idx = 0
    nest_targets = []
    for street_key, side in street_pool:
        if amen_idx >= len(amenity_ids):
            break
        amen_id = amenity_ids[amen_idx]
        recipe = recipes.get(amen_id)
        if not recipe:
            amen_idx += 1
            continue
        if _recipe_uses_player_shop(recipe):
            stamped = _stamp_player_shop_fixture(
                rooms, spec, amen_id, recipe, street_key,
                zone_id, city, fixture_manifest,
            )
        else:
            sx = rooms[street_key]["layout"]["x"]
            sy = rooms[street_key]["layout"]["y"]
            if side == "west":
                x, y, to_street = sx - 1, sy, "east"
            else:
                x, y, to_street = sx + 1, sy, "west"
            stamped = _add_side_room(
                rooms, names, recipe, x, y, street_key, to_street, zone_id, city,
                amenity_id=amen_id,
            )
        if recipe.get("nest_up"):
            nest_targets.append((stamped, recipe["nest_up"]))
        amen_idx += 1

    for base_key, nest_kind in nest_targets:
        _nest_up(rooms, base_key, nest_kind, zone_id, city, recipes.get(""))

    # Highway arms when template is not hamlet-only.
    template = _townforge_resolve_spine_template(spec)
    if template != "hamlet" and highway_len > 0:
        prev = square_key
        for i in range(1, highway_len + 1):
            key = f"Highway East {i}"
            _add(rooms, _room(
                key,
                f"Asphalt east of {city} (marker {i}).",
                zone_id, i, 0, z=0, city_name=city,
                outdoor=True, area_type="highway",
                title=_title(city, "Highway", f"East {i}"),
            ))
            _dig(rooms, prev, "east", key)
            prev = key
        prev = square_key
        for i in range(1, highway_len + 1):
            key = f"Highway West {i}"
            _add(rooms, _room(
                key,
                f"The old highway west of {city} (marker {i}).",
                zone_id, -i, 0, z=0, city_name=city,
                outdoor=True, area_type="highway",
                title=_title(city, "Highway", f"West {i}"),
            ))
            _dig(rooms, prev, "west", key)
            prev = key

        nb_x = -(highway_len + 1)
        nb_key = f"{city} Neighborhood"
        _add(rooms, _room(
            nb_key,
            f"A residential crossroads west of town — streets branch out. "
            "Stock homes with populate neighborhood.",
            zone_id, nb_x, 0, z=0, city_name=city,
            outdoor=True, area_type="city_street",
            title=_title(city, "Neighborhood", "Hub"),
            resources=["social"],
        ))
        _dig(rooms, f"Highway West {highway_len}", "west", nb_key)

        arm_defs = [
            ("north", "Neighborhood N", 0, 1),
            ("south", "Neighborhood S", 0, -1),
        ]
        if template == "college_grid":
            arm_defs.extend([
                ("east", "Neighborhood E", 1, 0),
                ("west", "Neighborhood W", -1, 0),
            ])
        for direction, prefix, dx, dy in arm_defs:
            prev_nb = nb_key
            for i in range(1, neighbor_arm + 1):
                key = f"{prefix}{i}"
                x = nb_x + dx * i
                y = 0 + dy * i
                _add(rooms, _room(
                    key,
                    f"{prefix}{i} — lawns and mailboxes off this block.",
                    zone_id, x, y, z=0, city_name=city,
                    outdoor=True, area_type="city_street",
                    title=_title(city, prefix, str(i)),
                ))
                _dig(rooms, prev_nb, direction, key)
                prev_nb = key

    # Sewer grid under square.
    sewer_arms = {"north": main_len, "south": main_len}
    if highway_len > 0:
        sewer_arms["east"] = highway_len + 1
        sewer_arms["west"] = highway_len + 1
    town_sewer = _sewer_plus(
        rooms, square_key, 0, 0, sewer_arms,
        zone_id=zone_id, city_name=city,
    )
    grate_links = []
    if len(main_n) > 1:
        grate_links.append((main_n[-1], f"{town_sewer} North 1"))
    if len(main_s) > 2:
        grate_links.append((main_s[1], f"{town_sewer} South 1"))
    if highway_len > 0:
        grate_links.append(("Highway East 1", f"{town_sewer} East 1"))
        grate_links.append(
            (f"Highway West {highway_len}", f"{town_sewer} West 1"),
        )
    _link_sewer_surface_grates(rooms, grate_links)

    # Threat: mild sewer nest at west tip when tagged.
    if "threat_nest_nearby" in (spec.get("tags") or []):
        west_tip = f"{town_sewer} West {highway_len + 1}" if highway_len else None
        if west_tip and west_tip in rooms:
            rooms[west_tip]["spawn_nest"] = "sewer"

    slug = spec["slug"]
    enter_alias = slug
    atlas = spec.get("atlas") or {}
    if atlas.get("enter_alias"):
        enter_alias = str(atlas["enter_alias"]).strip().lower()

    hub = entrance_key or square_key
    doc = {
        "id": slug,
        "plane": "earth",
        "realm": "prime",
        "autoload": False,
        "runtime_hub": hub,
        "runtime_enter_as": [enter_alias, slug, city.lower()],
        "runtime_visible_as": f"{city}",
        "city_name": city,
        "region": spec.get("region"),
        "city_color": style["city_color"],
        "sub_color": style["sub_color"],
        "cadence_lite": spec.get("cadence_mode") == "lite",
        "townforge": True,
        "townforge_build_complete": False,
        "townforge_size": spec.get("size"),
        "tags": list(spec.get("tags") or []),
        "player_shop_fixtures": fixture_manifest,
        "_comment": (
            f"Townforge spine for {city} — not ship-complete until "
            f"gm townforge build finishes (populate + shop wire + audit)."
        ),
        "pockets": [],
        "rooms": list(rooms.values()),
    }
    return doc
