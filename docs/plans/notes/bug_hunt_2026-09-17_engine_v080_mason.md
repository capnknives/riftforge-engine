# Engine v0.8.0 — Mason content (basegame / classic proof consumers)

**Date:** 2026-09-17 (America/Chicago)  
**Lane:** FIND-ONLY · proof-consumer **content gaps only** (Notbigville + Millbrook)  
**Checkout:** `/workspace/riftforge-engine`  
**Tip (this dig):** `45fe564` (`v0.8.0-2-g45fe564` — docs fold Patch/Grok into hunt)  
**Tag tip:** `v0.8.0` → `282ac8fe2f792a13199ad66d9255b8668614f82c`  
**Tracks:** https://github.com/capnknives/riftforge-engine/issues/3 · draft https://github.com/capnknives/riftforge-engine/pull/4  
**Sister:** Ash `/workspace/riftforge-ash/briefs/2026-09-17-engine-v080-hunt.md` (E080-01…12)  
**Hard no:** no fixes · no push · skip webclient (Grok) · skip CI (Patch)

---

## Inventory (v0.8.0 tree)

### basegame/ — Notbigville proof

| Area | What ships |
|------|------------|
| **Zones** | `notbigville.json` — 17 rooms (`NB00001`–`NB00014` + dig alcoves `HE00001`/`HE00002`/`HE31961`); `rift_nexus.json` — 5 rooms (`RN00001`–`RN00005`) |
| **Maps** | `earth_america.json` — CONUS overland + **2 pockets** (Notbigville→`NB00001`, Rift Nexus→`RN00001`) + 7 charter strips (Seattle/LA/Denver/Dallas/Chicago/NYC/Miami); `stellar_orbit.json` — 2 rooms |
| **NPCs** | `npcs/notbigville.json` — Ellis Quinn (`NB00004`), June Hale (`NB00011`); personas Operator at Post Office |
| **Items** | `items.json` — 11 fishing/junk + **`ritual_chalk`**, **`devils_trap_paint`** (devil's-trap copy) |
| **Catalogs** | `fishing_tables.json`, `appearance.json`, `personas.json`, `player_tips.json`, `vehicles.json` (cart), `nests.json`, `quests/fetch_pebble.json`, `bestiary/prairie_critter.json`, `climate/conus_normals.json` |
| **Kinds** | `room.basegame.indoor.json`, `room.basegame.outdoor.json` only |
| **Help** | `help_topics.py` — **30** topics; `help_engine_topics.py` — 7 engine meta |
| **Chargen** | Paths: `detective` / `medic` / `laborer` / `ranger` / `reporter` + Mundane / registered Alien origin |

### classic/ — Millbrook proof

| Area | What ships |
|------|------------|
| **Zones** | `millbrook.json` — 10 rooms (`MB00001`–`MB00010`), zone `kind: zone.classic.pocket` |
| **Maps** | `wilderness.json` — 9 rooms, `kind: map.classic.wilderness` |
| **Catalogs** | `catalog/classes.json`, `catalog/spells.json`; `bestiary/classic_wilderness.json` |
| **Kinds** | 10 profiles (zone/room/map/creature/catalog.*) |
| **Help** | `classic` + `score` only — **zero** TV/SUPERS proper nouns |
| **TV scan** | Clean |

Exit graph (basegame zones+maps rooms[]): **0 broken intra-tree exits**. Classic not deeply exit-audited beyond schema presence; smoke path is the gate.

---

## Boot without supers catalogs?

| Check | Result |
|-------|--------|
| Ash import / JSON_OK | Confirmed — `basegame` / `classic` import without `supers/` |
| Content roots | Self-contained under `basegame/content/` and `classic/content/` — no runtime path into `supers/content/` |
| Engine folklore kernels | Registered from basegame bootstrap (fishing/lockpick/grace/vessel/appearance/phone) against **local** JSON |
| Gap vs help / map prose | **Large** — help + America grid still narrate SUPERS towns, Paths, afterlife rooms that are **not** in proof trees |
| pip pin (E080-02) | Corroborate: wheel omits these trees entirely → “proof consumer” only via checkout/`PYTHONPATH` |
| lean account heal (E080-01) | Corroborate content-adjacent: lean/basegame/classic boot skips account reconcile when `supers` missing |

**Verdict:** Proof consumers **can** load/play their authored JSON without supers catalogs. They **cannot** match what `help` and overland cell copy promise. That is the content gap, not a missing kind file for Millbrook/Notbigville cores.

---

## Corroborate (do not re-file NEW)

| ID | Content-adjacent note |
|----|----------------------|
| **E080-01** | Ungated `supers.boot_migrations` — lean/basegame/classic account heal abort; content boot still reaches rooms but ops heal path is dead |
| **E080-02** | `engine*` packaging — docs call basegame/classic “shipped”; pin install does not deliver content trees |

---

## NEW findings (continue Ash E080-NN)

| ID | Sev | Symptom | Evidence |
|----|-----|---------|----------|
| **E080-13** | **M** | Release note understates help scrub: basegame help is still a **SUPERS topic dump**, not “some TV names” | `README.md` / `ENGINE_CONSUMER.md`: “TV-noun catalogs/help scrub is planned”. `basegame/help_topics.py` (~875 lines, 30 topics). Proper nouns still live: **Lebanon**, **Lawrence**, **Winchester Sam/Dean**, **Charlie Bradbury**, **Men of Letters** bunker, **Roadhouse**, **Singer House** / **Bobby**, **Radioshack**, **angel blade**, **The Colt**, dig example **MT00002**. Hot topics: `phone`, `traps`, `origins`, `vessel`, `purgatory`, `dig`, `relationships`, `breach`. ~**99** `help <topic>` cross-refs to pages **not** defined in basegame/classic help (angel, demon, soldier, deal, lodging, death, …). |
| **E080-14** | **M** | `help origins` (and path hubs) teach Awakened Monster/Celestial chargen; live chargen is Mundane jobs + Alien only | Chargen `PATH_ORDER` = detective/medic/laborer/ranger/reporter (`basegame/chargen.py`). Help origins body still: mortal vs Awakened, `help vampire` / `help angel` / `help hunter` / `help soldier` / episode pantheon. Player reading help before/after chargen gets a different game than Notbigville. |
| **E080-15** | **M** | America overland copy advertises `enter lawrence` / `enter stull` / etc.; proof pockets are only Notbigville + Rift Nexus | `earth_america.json`: **2** `pockets[]` hubs. Cell prose still has **Lawrence Approach** / **Stull Approach** (“Type enter lawrence…”, “enter stull…”). ≥16 distinct `Type enter <x>` hints (lawrence, stull, seattle, dallas, reaper, parish, asylum, mafia, …). Charter `rooms[]` strips exist for 7 metros — **not** for Lawrence/Stull/reaper/parish/lloyds/asylum/orchard/mirror. Dead enter for players who trust map text. |
| **E080-16** | **L** | Proof item catalog ships devil's-trap **TV naming** (folklore peel OK; noun scrub not done) | `items.json`: `ritual_chalk` desc “drawing devil's traps”; `devils_trap_paint` “permanent devil's trap”. `fishing_tables.json` `_comment`: “stripped SUPERS shape…”. Matches planned catalogs pass; call out so scrub scope includes items not only help. |
| **E080-17** | **L** | Room rows omit `kind` despite shipped kind templates | All 17 Notbigville rooms `kind: null`; Millbrook rooms `kind: null` (zone has `zone.classic.pocket`). Templates exist: `room.basegame.{indoor,outdoor}`, `room.classic.{indoor,outdoor}`. Classic `content_validate._room_kind` heuristics may mask at validate time; authored JSON still incomplete vs kind-first docs. |
| **E080-18** | **I** | Dig demo alcoves left in Notbigville Saloon up-stack | `HE00001`, `HE00002`, `HE31961` — all titled “H9 Demo Alcove”, exit down→`NB00011`. Odd high VNUM `HE31961`. Not SUPERS leakage; proof-town hygiene leftover. |

### Not bugs / out of lane

- Classic Millbrook+wilds+catalogs: **no TV names**; schema-first kinds present — keep.
- Intra-tree exits for basegame rooms[]: clean.
- Cadence town NPC homes (Ellis/June) resolve to authored NB keys.
- Webclient / CI / packaging mechanics → Grok / Patch / Ash E080-02 (corroborate only).
- Engine folklore kernels themselves (grace/vessel/traps code) — in scope only where **proof content/help** oversells them against missing rooms/paths.

---

## Release-note expansion (for catalogs pass)

Ship text today: *“basegame/ help still contains some TV proper nouns until the planned folklore catalogs pass.”*

**Expand to scrub checklist:**

1. **Help topics to rewrite or gate for basegame:** `origins`, `phone`, `traps`, `vessel`, `purgatory`, `planes`, `domain`/`demesne(s)`, `dig`, `breach`, `relationships` — either Notbigville-scoped copy or drop until Path catalogs exist.  
2. **Proper-noun denylist (proof tree):** Lebanon, Lawrence, Stull, Winchester, Charlie Bradbury, Men of Letters, Roadhouse, Singer House, Bobby (SPN), Radioshack-as-Lebanon, MT00002, angel blade, The Colt, Waystation / Ashen Marches / Black Nest (purgatory help).  
3. **Map:** strip or re-pocket Lawrence/Stull Approach cells; align every `Type enter X` with an actual `pockets[]` / strip room.  
4. **Items:** rename devil's-trap strings to folklore-neutral occult marks (or keep mechanics, scrub nouns).  
5. **Kinds:** stamp `kind` on Notbigville / Millbrook room rows.  
6. **Dig examples:** replace `MT00002` with `NB00001` / `MB00001`.

---

## Stamp-ready table (PR #4)

| ID | Sev | Lane | One-liner |
|----|-----|------|-----------|
| E080-13 | M | content/help | basegame help still SUPERS/TV dump (Lebanon/Winchester/MoL/…); ~99 dead help cross-refs |
| E080-14 | M | content/help | help origins ≠ chargen (Awakened Paths documented; only Mundane jobs+Alien live) |
| E080-15 | M | content/map | earth_america enter lawrence/stull/… with only 2 pockets (Notbigville+Nexus) |
| E080-16 | L | content/items | ritual_chalk / devil's-trap paint TV nouns in proof catalog |
| E080-17 | L | content/kinds | Notbigville + Millbrook room rows missing `kind` despite templates |
| E080-18 | I | content/hygiene | HE* H9 Demo Alcove dig leftovers above Saloon |
| E080-01 | H | corroborate | lean account heal / ungated supers import (Ash) |
| E080-02 | M | corroborate | pip pin omits basegame/classic trees (Ash) |

**Top for parent:** **E080-13** + **E080-15** (player-visible proof lies) · **E080-14** (chargen/help split) · corroborate **E080-01/02**.

**Brief path:** `/workspace/riftforge-ash/briefs/2026-09-17-engine-v080-mason-content.md`
