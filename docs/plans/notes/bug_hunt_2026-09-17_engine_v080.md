# Engine bug hunt — public **v0.8.0** vs **v0.7.0** (+ webclient “some users”)

**Date:** 2026-09-17 (America/Chicago)  
**Checkout:** `/workspace/riftforge-engine` (find-only; no commits/PRs/live ops)  
**Tag tip:** `v0.8.0` → **`282ac8fe2f792a13199ad66d9255b8668614f82c`** (`Sync export from RiftForge monorepo for v0.8.0.`)  
**Baseline:** `v0.7.0` → `63b6d635b33f3942a88f423a7e1d59ad640e79c3`  
**Method:** `git fetch origin --tags`; detached at `v0.8.0`; `git diff v0.7.0..v0.8.0`; purity `rg`; import proofs; `python3 tools/engine_smoke.py`; static webclient + `engine/web_client.py` + GMCP read.

---

## Diff summary (v0.7.0 → v0.8.0)

| Theme | What changed |
|-------|----------------|
| **Folklore kernels** | New/expanded: `cadence_kernel`, `fishing`, `locks`, `occult_marks`, `pocket_grid`, `vessel`/`grace`, `planes/policy`, trivia channel/corpus/session |
| **Maps / travel / HUD** | Large `overland`, `vehicles`, `room_vnum`, `world_maps`, `viewport`, zone walk + Map.Here/View client |
| **GMCP** | `Room.Occupants`, `RiftForge.Map.Here` / `.View`, `client_supports_exact`, supports-refresh debounce |
| **Web client** | `app.js` +462/−14 lines: atlas paint coalesce, path-walk, occupants dots, WS drain queue; SW/index cache stamp → `2026-09-07-pathwalk` |
| **Ops / copyover** | `session_bind`, `pfile_restore`, `item_index`/`item_inum`, boot profile WAL probe, account-heal rewrite in root `server.py` |
| **Proof consumers** | `basegame` content (items/fishing/tips/npcs), lifestyle/lockpick/traps verbs; classic untouched layout |
| **Docs / pin** | UPGRADING / RELEASING / ENGINE_CONSUMER → pin **`@v0.8.0`**; copyover reload order adds `engine.char_identity` |
| **CI** | Still single `.github/workflows/ci.yml`: push + PR → editable install + `engine_smoke` / `basegame_smoke` / `classic_smoke` (Python 3.11). **No** `release:` / tag-only job. Folklore kernel smokes nested under `engine_smoke`. |
| **Scale** | **171 files**, +25347 / −2375 |

---

## Purity (Phase 2 claim)

| Check | Result |
|-------|--------|
| Live `^\s*(import supers\|from supers)\b` under `engine/` | **PURE** — zero hits |
| Mentions of `supers` in `engine/` | Comments / migration notes only (hooks, world, verbs, content_store, …) |
| `python3 tools/engine_smoke.py` | Prints `engine_smoke_ok` (also surfaces lean-boot accounts exception — see E080-01) |

---

## Proof consumers (import / JSON; no long servers)

| Target | Result |
|--------|--------|
| `engine`, folklore systems, `session_bind`, `web_client` | Import **OK** (py 3.13.5 on box) |
| `basegame`, `classic`, `*.bootstrap`, `*.seed`, `game_select` | Import **OK** |
| New basegame JSON (`items`, `fishing_tables`, `player_tips`, npcs/zones) | **JSON_OK** |
| Classic `content/items.json` / `vehicles.json` | **N/A** — classic uses `content/catalog/`, `kinds/`, `maps/`, `zones/` (not those filenames) |
| Default `game_select.game_name()` with no `supers/` | **`none`** (auto lean) |
| **Packaging footgun** | `pyproject.toml` `[tool.setuptools.packages.find] include = ["engine*"]` — **`pip install …@v0.8.0` does not ship `basegame/` or `classic/`** despite docs calling them shipped proof consumers |

---

## CI on tags / releases

- Triggers: `push`, `pull_request` only.
- Tag push of `vX.Y.Z` is a GitHub `push` to `refs/tags/…` → smoke **can** run if Actions enabled; there is **no** dedicated release workflow, artifact, or tag matrix.
- Does **not** prove live SUPERS pin / dual-mount / WSS nginx path.

---

## UPGRADING / RELEASING pin footguns

| Footgun | Evidence |
|---------|----------|
| SUPERS must bump `supers/pyproject.toml` to `@v0.8.0` | `docs/UPGRADING_RIFTFORGE.md` example pin; stale `@v0.7.0` → miss folklore kernels + Occupants GMCP |
| Copyover reload order | `ENGINE_CONSUMER.md`: must `reload(hooks)` → **`reload(char_identity)`** → `reload(persistence)` → blob re-register; skip identity → copyover abort after Veil (live 2026-09-10) |
| Wheel ≠ full tree | Editable/`PYTHONPATH` checkout gets basegame+classic; **tag pin via pip gets `engine` only** |
| Optional TLS pair | `RIFTFORGE_WSS_CERT` + `RIFTFORGE_WSS_KEY` both required; half-set → cleartext WS |
| Breaking hook API = major | Semver note in RELEASING; Occupants is additive but clients listing parent `Room` must **not** get Occupants (exact-match gate) |

---

## Findings (NEW, capped ~12)

| ID | Sev | Symptom | Evidence |
|----|-----|---------|----------|
| **E080-01** | **H** | Lean / basegame / classic boot: entire account reconcile/heal block skipped; log `[server] account reconcile/migrate failed:` + `ModuleNotFoundError: No module named 'supers'` | `server.py` ~637–735: top-level `from supers import boot_migrations` **unguarded** by `_HAS_SUPERS`; `except` swallows. v0.7.0 called `reconcile_accounts` / `migrate_legacy_gm_ranks` without that import. Reproduced under `tools/engine_smoke.py` on this tip. |
| **E080-02** | **M** | `pip install riftforge @ …@v0.8.0` consumers lack basegame/classic proof trees; docs imply they “ship with” the public engine | `pyproject.toml` packages `engine*` only; tree still contains `basegame/` + `classic/` for path/editable installs |
| **E080-03** | **M** | Some browsers keep **stale web client** (missing atlas-wsdrain / walk / Occupants UI) while stamp/docs claim newer | `CLIENT_BUILD = "2026-09-16-atlas-wsdrain"` in `app.js`, but `index.html` loads `app.js?v=2026-09-07-pathwalk`, SW `CACHE_BUILD = "2026-09-07-pathwalk"`; `style.css?v=2026-09-05c` never bumped in this tag. Network-first SW + `Cache-Control: no-cache` mitigates online; offline / sticky intermediary / SW race → split cohorts |
| **E080-04** | **M** | Safari / iOS: connected UI but dead inbound, or flap on wake / bfcache | `app.js` `connect()` closes leftover OPEN sockets (Safari tab-sleep); `onPageShow` + `ensureOpenSocket`; comments cite live `:4080` flap. Cohort = WebKit, not Chromium desktop |
| **E080-05** | **M** | HTTPS play page + plain `ws://` (or reverse-proxy path mismatch) → connect fail for TLS-only browsers | `wsUrl()` = `wss:` iff `location.protocol === "https:"`, else `ws:` + **`host + "/"` only** (ignores pathname). Gateway TLS needs both `RIFTFORGE_WSS_*`. Nginx TLS-terminate / subpath deploy fails for *some* hosts |
| **E080-06** | **M** | After long background tab / copyover burst: UI freeze or delayed text/maps | New `queueWsFrame` / `drainWsFrames` (~8 ms/frame); `document.hidden` uses `setTimeout(0)`. Backlog drains slowly for heavy Occupants+Atlas+Zone users |
| **E080-07** | **L** | Click-to-walk hangs mid-path (“Walking …”) until user types / compass | `continueWalkFromZone` waits for Zone `you` == `walkExpect`; failed move / missing Zone refresh never advances; only `cancelWalk` on cmd/compass/hide |
| **E080-08** | **L** | Phone / MudRammer-class clients: giant glyph lines or Occupants spam if Supports inheritance wrong | Mitigated in-engine by `client_supports_exact("Room.Occupants")` + `_wants_mapper_gmcp` (bug 1221); regression risk if a client lists parent `RiftForge.Map` but cannot render Atlas |
| **E080-09** | **L** | Copyover / dual-checkout: Veil announce then abort if pin/reload omits `char_identity` | Documented in `ENGINE_CONSUMER.md` (2026-09-10); pin bump alone insufficient without reload-order discipline |
| **E080-10** | **L** | Low-end mobile: atlas jank / memory pressure after Here spam | Atlas country layer offscreen canvas + rAF coalesce (`paintAtlas`); dpr in sig — good; still allocates full country bitmap |
| **E080-11** | **L** | CI green on tag ≠ folklore smokes as separate jobs; Python 3.11 CI vs local 3.13 | `.github/workflows/ci.yml`; folklore scripts asserted inside `engine_smoke` only |
| **E080-12** | **I** | No CSP on static play page (shared-host / XSS blast radius differs by browser extension set) | `engine/web_client.py` headers: `Content-Type`, `Content-Length`, `Connection`, `Cache-Control: no-cache` only — no `Content-Security-Policy` |

---

## Webclient — ranked hypotheses (“works for some users”)

1. **E080-03** cache-bust / SW stamp desync (stale `app.js` cohort)  
2. **E080-04** Safari/iOS silent WS + bfcache  
3. **E080-05** mixed content / missing WSS / subpath `wsUrl`  
4. **E080-06** WS drain backlog after hitch (looks “stuck” until frames catch up)  
5. **E080-07** path-walk stuck (looks broken only for click-to-walk users)  
6. Service worker offline fallback after flaky mobile network (shell OK, live WS not)  
7. Private / locked-down storage (IDB has 400 ms timeout before `connect()` — usually OK; localStorage prefs silent-fail)  
8. `webkitStoryPaint` vs Chrome `display:none` compositor bust — wrong branch → blank story while cmds still send (UA edge cases)  
9. Corporate TLS inspection / WebSocket blocked (telnet users fine)  
10. Old Mudlet/MudRammer Supports set without Occupants/Here (HUD incomplete, not full fail)

**Not primary auth bugs:** browser client has no token auth — game login is in-band WS text; `web_client.py` only serves allowlisted static + optional sidecars.

**Map.Here / Map.View:** client handlers present (`applyMapHere` / `applyMapView` → Here); Supports lists both; server `push_map_here` / `push_map_view` gated on Supports + screenreader. Browser View intentionally reuses Here (country bitmap, not Mudlet camera glyphs).

---

## Top IDs for parent

**Tip SHA:** `282ac8fe2f792a13199ad66d9255b8668614f82c` (`v0.8.0`)  
**Top:** **E080-01** (H — ungated `supers.boot_migrations` aborts lean account heal), **E080-03** / **E080-04** / **E080-05** (webclient some-users), **E080-02** (pip pin packaging).  
**Purity:** Phase 2 `engine/` claim holds (no live supers imports).  
**Brief path:** `/workspace/riftforge-ash/briefs/2026-09-17-engine-v080-hunt.md`


## Teammate folds

### Patch CI/smoke — CLOSED @ `282ac8fe`
Brief: `briefs/2026-09-17-v080-patch-ci.md`

| ID | Sev | Note |
|----|-----|------|
| **E-1** | high | heal path can abort; CI still green (pairs E080-01) |
| **E-2** | high | clean pip wheel drops `static/` → `GET /` 500 Missing static file (**webclient break class**) |
| **E-3** | med | basegame/classic not in package (pairs E080-02) |
| **E-4** | med | no webclient CI |
| **E-5** | med | no branch protection / release workflow |
| **E-6** | low | stamp desync (pairs E080-03) |
| **E-7** | low | extra smokes not gated in CI |
| **E-8** | info | Release has empty assets |

CI: only `engine-smoke` green. Local: packaging_smoke FAIL without supers; wheel install → static 500.

### Grok door — CLOSED
https://github.com/capnknives/riftforge-engine/pull/4#issuecomment-5718033417

Corroborates **E080-03…07**. Top some-users split: stamp desync. Addenda: Safari OPEN-zombie not healed on pageshow; **E080-08** unversioned `discord-invite.js`.


### Mason content — CLOSED (+ E080-13…19)
https://github.com/capnknives/riftforge-engine/pull/4#issuecomment-5718069870 · addendum #5718076555 · brief `briefs/2026-09-17-engine-v080-mason-content.md`

| ID | Note |
|----|------|
| **E080-13** | basegame help still teaches Lebanon/Winchester/Purgatory/Chuck (Notbigville proof) |
| **E080-14** | help origins ≠ chargen (reconciled) |
| **E080-15…18** | dead enter lawrence/stull, chalk TV nouns, kind null, HE* alcoves (see Mason brief) |
| **E080-19** | Millbrook Inn/Smithy/Temple shells but **0** town NPCs |

Exit graphs CLEAN. Corroborates E080-02 packaging.

## Lanes — ALL CLOSED

Ash · Patch · Grok · Mason on https://github.com/capnknives/riftforge-engine/pull/4

**P0 call:** E080-01 / E-1 heal supers import · **E-2** wheel drops static → GET / 500 · E080-03 stamp desync · E080-13 TV help on Notbigville · E080-19 empty Millbrook NPCs.
