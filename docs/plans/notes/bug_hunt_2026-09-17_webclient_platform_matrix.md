# Webclient platform / browser matrix (find-only)

**Date:** 2026-09-17 CT  
**Tip:** engine `v0.8.0` @ `282ac8f` + live `https://play.riftforge.me/client/`  
**Tracks:** riftforge-engine #3 / #4 · pairs Patch WC-OPS-1/2 · Ash E080-03…07

## Live facts (curl)

| Check | Result |
|-------|--------|
| `GET https://play.riftforge.me/client/` | **200** — loads `app.js?v=2026-09-07-pathwalk`, `style.css?v=2026-09-05c` |
| Live `CLIENT_BUILD` inside that `app.js` | `2026-09-16-atlas-wsdrain` (**desync**) |
| Live SW `CACHE_BUILD` | `2026-09-07-pathwalk` |
| `https://riftforge.me` TLS | **Cert CN=`*.github.io`** — name mismatch (Patch WC-OPS-2) |
| `wsUrl()` from `/client/` page | `wss://play.riftforge.me/` (host root — OK if nginx splits `/client/` static vs `/` WS) |

**Can't verify:** real Safari/iOS/Firefox device sessions (no browser farm). Matrix is code-path + live stamp.

## Cohort matrix

| Cohort | Symptom | Likely ID |
|--------|---------|-----------|
| Returning Chrome/Edge/Firefox + SW | Stuck on old HUD / missing features | E080-03 / WC-OPS-1 |
| Fresh Chromium | Usually works | — |
| Safari / iOS Safari / Chrome-on-iOS | Connected, no new lines; flap after sleep | E080-04+ (zombie OPEN) |
| iOS PWA / home screen | Same + harder SW clear | E080-03+04 |
| Android Chrome | Keyboard crush / pathwalk hang | E080-06/07 + visualViewport |
| Firefox private | Often no SW → fewer stale cases | soft |
| Apex `https://riftforge.me` | TLS error / wrong site | WC-OPS-2 |
| pip wheel install | GET / 500 missing static | E-2 |
| Subpath / wrong-host proxy | Immediate WS fail | E080-05 |

## Fix order
1. Unify live stamps (CLIENT_BUILD === ?v= === CACHE_BUILD)
2. Players use `play.riftforge.me/client/` only (never apex)
3. Safari frame-watchdog / force reconnect on wake
4. Wheel ships `static/`
5. Pathwalk softlock / drain backlog

## Diag
D-WC-1 stamp-triad smoke · D-WC-2 build stamp + clear-site-data hint · D-WC-3 browser/OS/URL capture · D-WC-4 Safari Connected-but-silent repro

Find-only.
