# AGENTS.md — Working in the Riftforge / SUPERS repo

Guidance for any AI coding assistant (Cursor, Copilot, Claude Code, etc.) **and** human
contributors. The authoritative design is `docs/SYSTEMS_DESIGN.md`; this file is the
short version of the rules every edit must respect. The tool-specific files
(`.github/copilot-instructions.md`, `.cursor/rules/`, `CLAUDE.md`) all point back here.

## What this project is

**Riftforge** is a from-scratch MUD **engine** — pure Python, `asyncio`. **SUPERS** is
the **game** built on it. The engine stays generic; SUPERS content layers on top.

The repo is one Git history with an internal `engine/` vs `supers/` package boundary
(not two separate repos yet — roadmap: `docs/plans/two_repo_purity.md`). Until that
split lands, see "Where things live" below for which files are on which side, and
which core files are still shared/undecomposed.

## Hard rules — do not violate without explicit discussion

1. **Python 3.11+ standard library only.** No third-party dependencies, no frameworks.
   Do NOT suggest or add **Evennia, Django, a Rust/Go rewrite, Redis, PostgreSQL, gRPC,
   or spaCy/NLP libraries.** All were considered and deliberately rejected as
   out-of-scope for a solo build (see `docs/SYSTEMS_DESIGN.md`).
2. **Pure-core / thin-shell.** `world.py` and game logic must never import or touch
   networking. Only `engine/connection.py` touches sockets. Keep I/O out of the domain
   model — it's what lets a web client be added later without rewriting the game.
3. **Single-threaded asyncio.** No threads, no locks. One command resolves before the
   next starts; rely on that guarantee instead of adding concurrency primitives.
4. **Composition over inheritance.** Attach data to `Character` (stats, hunger,
   corruption, known Disciplines) as components. Do NOT build a class tree per Origin or
   Discipline.
5. **Combat is two layers, always.** Combat math resolves into a *Structured Battle
   Brief* (data). Prose rendering reads that brief as a **separate** step. Never merge
   the math and the text — the split is what makes the cinematic renderer a later swap.
6. **Follow the phased build order** (`docs/SYSTEMS_DESIGN.md` §9): generic engine
   first, SUPERS content second, cinematic combat last. Don't build a later phase before
   the earlier one runs.
7. **Accessibility is a first-class constraint.** Never signal meaning by color alone;
   keep output client-wrappable (no forced line-wrapping); keep critical info out of
   ASCII art.
8. **Logout ≠ deletion (architectural invariant).** A character that logs off is **not**
   removed from the world — it becomes an **Echo** (Session detached, body persists).
   Never write code that deletes, despawns, or loots a character on disconnect.
   Offline Echoes **can be injured** (Evil Strikes Back → Town Clinic via
   `supers/hospital.py` at 0 HP); they are never killed-for-loot or deleted. Opt-in
   non-lethal Echo sparring is D24 (`sparaccept`). See SYSTEMS_DESIGN.md §4-E and
   `HANDOFF.md`'s "Next up" / "Already shipped". Implemented as designed:
   `engine/connection.py`'s `disconnect()` detaches the Session and converts the
   character to an Echo; it has never deleted a character since persistence landed
   (Milestone 2).
9. **Immersion parity (NPCs ↔ players ↔ Echoes).** The town should feel like one shared
   world, not two codepaths. Watchers must see the **same room broadcasts** when an NPC
   or Echo does something a player would type (`work`, `rob`, `form`, `bite`, `buy`,
   `drink`, …). Actors should be able to do what players can, and players what Cadence
   actors can, for the same verbs — gates (true form before living feeds, etc.) must not
   drift. Offline Echoes run Cadence lifestyle AI for that reason: simulating life and
   roleplay, not a parallel silent simulation. Prefer shared domain helpers and/or
   `engine.npc_act.npc_do` (same `COMMANDS` table); do **not** invent NPC-only shortcuts
   that look different in the room or skip player rules. Cadence may still *plan* with
   domain APIs (pathfinding, need meters) — boiling
   every planner into telnet dispatch is not required — but when a verb exists for
   players, the *observable* outcome for watchers should match. Survival needs
   (food/water/sleep/hygiene) go through `npc_do` / shared helpers so room text
   matches `buy`/`eat`/`drink`/`sleep`/`wash`. See
   `docs/SYSTEMS_DESIGN.md` §4-E and `engine/npc_act.py`.
10. **New features in a separate git worktree.** Multi-file features / new systems land
    in a linked `git worktree` on a feature branch — not in the primary checkout that
    may be running Docker bind-mounts or other WIP. This applies to **planning and
    implementing**: plans must name the worktree path and branch; agents must not
    assume edits land on primary `main` unless the maintainer explicitly says to
    work in-place / on this tree / on main. Create the worktree from `origin/main`
    (or the agreed base), implement and smoke-test there, then merge or open a PR.
    Always-apply Cursor rule: `.cursor/rules/feature-worktree.mdc`. Tool-specific
    instruction files (`.cursor/rules/`, `CLAUDE.md`, `.github/copilot-instructions.md`)
    point here. **Exceptions:** tiny one-file typo or docs-only fixes, and hotfixes
    the maintainer explicitly asks to do in-place.
11. **Ship player help with the feature — not later.** A new player-facing Origin,
    Path/Background, Discipline, system loop, or verb is **not done** until help
    ships in the **same PR / merge**:
    - Every new/renamed command: `(handler, help_text)` in `COMMANDS` (one-liner).
    - Every multi-command loop, multi-verb feature, Origin/Path/Background, or
      concept beyond one sentence: `HELP_TOPICS` page(s) with a plain **How you
      play** (or equivalent) — what to type, in order — plus `HELP_CATEGORIES`
      listing for primary hubs; smoke coverage for new topics. Trivial verbs
      (look, get, say, who, quit, …) stay one-liners only.
    - Do **not** merge code that players can type while `help <name>` still
      misses or only points at a design doc path. Unbuilt systems get topics
      when they ship, not before. Template tone: `docs/plans/help_rewrite.md`;
      Divine / Vampire hubs are the shape. Always-apply:
      `.cursor/rules/helpfiles-required.mdc`.

## Conventions

- 4-space indent, `snake_case`.
- One command handler per verb in `commands.py`, signature `def cmd_x(character, args, game)`.
- Every verb in `commands.py`'s `COMMANDS` dict is a `(handler, help_text)` pair, not a
  bare handler — a command isn't done until it has a one-line `help_text` too. `help`
  and `commands` are both generated straight from `COMMANDS`, so there is nowhere else
  to add or edit help text; this is what makes it impossible for the help output to
  silently drift out of sync with what's actually dispatchable.
- **System topic pages** follow hard rule 11 above. Put longer pages in
  `help_topics.py`'s `HELP_TOPICS` dict (re-exported from `commands.py` for existing
  imports). Cross-link with `See also: help …`; point related `COMMANDS` one-liners
  at the topic (`see 'help …'`); keep lines client-wrappable; never signal by color
  alone. Player pages: no GM `set`, ticket ids, or `docs/…` paths.
- **Builder / content-authoring topics** are also allowed for multi-concept authoring
  systems (maps, NPC rosters, item catalogs, etc.). Keep them under the **Builder**
  category with `help content` / `help build-*` names so they never collide with
  player gameplay pages (`help origins`, `help map`, `help relics`, …). Repo SoT for
  paths and schemas: `docs/CONTENT_AUTHORING.md` + `docs/templates/`.
- **`help` and `commands` are separate indexes:** bare `help` shows categorized
  `HELP_TOPICS` only; `commands` lists every verb's one-liner. Do not merge them.
- The server emits `\r\n` line endings (telnet); source files themselves use LF.
- Prefer data-driven content (JSON) over hardcoding game design into core code.

## Commenting standard (this is a learning project)

The maintainer is learning, so **code clarity beats brevity** — comment generously:

- Every module, class, and function gets a docstring saying what it does (and why, if
  it isn't obvious).
- Add inline `#` comments explaining the intent of each non-trivial line.
- The first time a Python feature a beginner might not know appears, **explain it** —
  `async`/`await`, list comprehensions, `isinstance`, dict dispatch, `.get()`, f-strings,
  tuple unpacking, local imports, type hints, etc.
- Comments should *teach*: explain the **why**, and for non-obvious lines the **what**.
- The one thing to avoid is pure noise (`i += 1  # add 1 to i`) — skip comments on
  truly self-evident lines so the meaningful ones stand out.

Match the comment density already used in the engine's `.py` files.

## Auto-deploy safety

Docker's `engine/auto_deploy.py` overlays squash-merged fix files onto the
bind-mounted checkout. That must not silently wipe pipeline integrations:

1. **Side effects live in `engine/`**, registered at import (`server.py` imports
   `bug_webhook`) or via dedicated helpers. Do **not** make
   `commands.py` the only place webhook URL/auth are read -- overlays of that
   file from older commits have already stripped such wiring once. Webhook
   POSTs themselves are GM-on-demand (`squashbugs`), not automatic on `bug`.
2. **`commands.py` = dispatch + help text.** Thin handlers may call into
   `engine/` helpers (`bug_filing`, etc.); keep the integration logic there.
3. **Pipeline changes must land on `origin/main`** before you rely on them in
   Docker. Local-only WIP fights every overlay.
4. **After adding pipeline code:** push → `docker compose restart` → confirm
   `squashbugs` is in `COMMANDS` and a GM `squashbugs` logs `[bug_webhook] POST ok`.
5. Auto-deploy is **advance-only** (new `origin/main` SHA). Catch-up without a
   remote advance: `tools/deploy_bug_fix.py --merged`. Protected paths:
   `AUTO_DEPLOY_PROTECT_PATHS` (pipeline modules) and
   `AUTO_DEPLOY_PROTECT_PREFIXES` (live-authored content:
   `jobs.json` / `personas.json` / `items.json` / `content/npcs/` — see
   `.env.example` / `tools/apply_pr_fix.py`). Dirty (uncommitted) files are
   also skipped. GM catalog edits rewrite JSON on disk; protect them so
   overlays cannot wipe in-game building. Toggle live with GM
   `autodeploy on|off` (writes `.auto_deploy_override`).
6. **Player announcements are Fix-only.** Only subjects like `Fix bug #N: …`
   queue the in-game countdown. Merge/feature commits that merely mention
   `bug #N` advance `origin/main` silently -- do not put incidental bug ids
   in non-fix subjects if you can help it; the parser is strict either way.
7. **If live stops picking up pushes:** check container logs for
   `[auto_deploy] git fetch skipped:` — that is a hard stop until `git fetch`
   works again (empty loose objects under `.git/objects` have caused this on
   empty-`.git`-object repair). Full diagnosis + repair: `docs/LIVE_DEPLOY.md`.

## Local Docker & live DigitalOcean (ops)

Canonical runbook: **`docs/LIVE_DEPLOY.md`**. Short version for agents:

- **Local Docker:** bind-mounts this checkout. Prefer host `py -3.13 smoke_test.py`.
  For offline DB tools (seeds): `docker compose stop` →
  `docker run --rm -v "${PWD}:/app" -w /app riftforge:latest python tools/…` →
  `docker compose start`.
- **Live DigitalOcean:** `root@162.243.50.82`, repo
  `/home/riftforge/riftforge`, telnet `:4000`. SSH key
  `~/.ssh/id_ed25519_do` + **`id_ed25519_do.passphrase`**. Agents must
  use `py -3.13 tools/live_ssh.py -- "…"` or `--deploy-log` (unlocked temp
  key) — **do not** invent `SSH_ASKPASS` / `ssh-agent` / bare `ssh -i`
  recipes from Cursor. Sync shared `bug_reports.log` / `suggestions.log`
  with `py -3.13 tools/sync_reports.py pull|push` so local and live do not
  double-dip bug IDs. **Never commit** private keys or passphrases.
  Full ship + merge checklist: `docs/LIVE_DEPLOY.md` (“Ship a fix onto live”).
- **Do not** SFTP a partial module set onto live to "force" a feature; get
  `origin/main` onto the tree first (auto-deploy or repair), then seed.
- After a push to `main`, confirm live with
  `py -3.13 tools/live_ssh.py --deploy-log` (fetch succeeding;
  feature commits `syncing working tree to …`, or Fix countdown).
- **GitHub CLI on this machine:** `gh` may be missing from `PATH` — call
  `& "C:\Program Files\GitHub CLI\gh.exe"`. When `main` is checked out in
  another worktree, `gh pr merge` fails locally; merge via
  `gh api -X PUT repos/capnknives/RiftForge/pulls/N/merge -f merge_method=merge`
  instead (see `docs/LIVE_DEPLOY.md`).

## Before you finish an edit

- Run `python smoke_test.py` — it must pass.
- If you added a command, give it a `help_text` in `COMMANDS` (see Conventions above)
  **and** update `smoke_test.py`.
- If you shipped a multi-command / multi-concept player system, add `HELP_TOPICS`
  page(s) (see Conventions above), point related one-liners at them, and cover the
  topics in `smoke_test.py`.
- Add a line under "Unreleased" in `CHANGELOG.md`. Stamp a hidden
  monotonic id and today's date inside the bold lead-in:
  `- **#N YYYY-MM-DD — Summary.**` where `N` is one more than the highest
  existing Unreleased `#` id (the in-game `changes` command sorts by
  that id; players only see the date).
- **Known intentional smoke_test stderr:** `[tick_loop] a tick raised…`,
  `INTENTIONAL_SMOKE_TEST_ONLY`, or `simulated tick crash` during smoke is
  **expected** from `tick_loop_resilience_test` (the test deliberately raises
  once to prove the heartbeat survives). Do **not** investigate or “fix” it
  unless smoke fails, or the message appears outside that test / without the
  intentional marker. Resilience is asserted via `call_count > 2`, not via a
  printed traceback (the test redirects that noise so terminals stay quiet).
- If you changed a `supers/training.py`/`supers/stats.py` tuning constant, run
  `python -m supers.balance_sim` and look at where the numbers land over simulated
  months — it's a report, not a gate,
  but it's the only thing that will show you a pacing regression before a live character
  does. Progression gains use `round(value, 2)`, never a plain `+=` — a real bug this
  simulation caught: repeated fractional additions can float-drift to a value that never
  reaches an int cap, permanently soft-locking a stat just below it.
  For **offline Echo** softcap / T0→T1 wall-time (casual vs grinder), also run
  `py -3.13 -m supers.echo_level_test --preset soldier --pacing` — see
  `docs/ECHO_LEVEL_TEST.md`.
- If you changed Cadence / needs / fuel / blood lifestyle constants or AI gates,
  also run `python -m supers.needs_timing` (SEEK/CRITICAL/full in game-h and
  real-h at 3x), then `python -m supers.cadence_audit` (and `balance_sim
  --scenario needs` / `blood`). Soft WARN lines flag stuck Echoes, Vampire
  food-rob leaks, or hunger↔fuel desync — it is a self-audit report, not a
  pass/fail gate.

## Agent shell (Windows / PowerShell)

The primary maintainer checkout runs Cursor’s `Shell` tool under **PowerShell**,
not bash. Agent-facing commit / git recipes in global Cursor user rules that
show bash heredocs (`cat <<'EOF'`) **do not work here** — use PowerShell
instead or the command fails before git runs.

**Do not use:**

- `git commit -m "$(cat <<'EOF' ... EOF)"` (bash heredoc)
- `git rev-parse --abbrev-ref @{upstream}` without quotes (PowerShell hashtable)

**Do use:**

```powershell
$msg = @"
Subject line.

Body explaining why.
"@
$msg | git commit -F -
```

or `git commit -m "Subject." -m "Body."`, and
`git rev-parse --abbrev-ref '@{upstream}'`.

Always run Python as **`py -3.13`** on this machine (see `HANDOFF.md`).
A matching always-on Cursor rule lives at `.cursor/rules/windows-shell.mdc`.

**GitHub CLI / live SSH (this machine):** `gh` may be missing from `PATH`
— use `& "C:\Program Files\GitHub CLI\gh.exe"`. If `main` is already
checked out in a linked worktree, merge PRs with
`gh api -X PUT repos/capnknives/RiftForge/pulls/N/merge -f merge_method=merge`
(not `gh pr merge`). Live key is passphrase-protected — use
`py -3.13 tools/live_ssh.py` (see `docs/LIVE_DEPLOY.md`); never invent
`SSH_ASKPASS` / `ssh-agent` recipes.

## Where things live

- **`engine/`** — generic, game-agnostic. Zero imports of anything SUPERS-specific
  at module level:
  `auth.py` (password hashing), `connection.py` (`Session`, sockets, login/reconnect),
  `copyover.py` (hot-reload), `reports.py` (bug/suggestion logging; File I/O only),
  `bug_webhook.py` (optional outbound POST for open bugs -- networking
  stays out of `reports.record()`; GM `squashbugs` / `fixbugs` calls
  `schedule_open_bugs`),
  `bug_filing.py` / `deploy_notify.py` / `auto_deploy.py` / `deploy_guard.py`
  (bug-report → webhook → in-game deploy pipeline), `watch_and_run.py` (Docker
  dev-loop auto-reload), `game_calendar.py` (game clock / seasons / lunar phase),
  `npc_act.py` (NPC/Echo verb dispatch via the same `COMMANDS` table),
  `style.py` (ANSI / client formatting helpers), `hooks.py` (game
  registration: Character attach, persist blob, chargen, help — see
  `docs/ENGINE_CONSUMER.md`), `verbs/` (generic MUD command
  handlers — `ENGINE_COMMANDS` merged by `commands.py`).
- **`supers/`** — the game built on the engine. Zero networking/socket code:
  `bootstrap.py` (registers hooks on the engine), `character_attach.py` (SUPERS field defaults attached at end of
  `Character.__init__`), `persist_blob.py` (SUPERS side of character save/load
  JSON), `stats.py` (the stat spine), `training.py` (training/sparring gains),
  `combat.py`/`combat_prose.py` (battle-brief math + cinematic prose, kept as
  separate layers -- `combat_prose.py` is the Phase 3 tagged-CFG renderer;
  `combat_lexicon.py` loads its word pools), `bestiary.py` (wilderness spawn
  tables), `content.py` (Origins/Disciplines catalog), `appearance.py`
  (structured look slots + auto-built description), `balance_sim.py`
  (long-horizon progression simulation, not a test), `echo_level_test.py`
  (Echo kit soak + `--pacing` real-life softcap/break ETA — see
  `docs/ECHO_LEVEL_TEST.md`), `cadence_audit.py`
  (Cadence lifestyle self-audit report, not a test), `needs_timing.py`
  (needs/fuel SEEK·CRITICAL·full table in game-h and real-h at 3x),
  `needs.py` / `cadence.py` / `pathfind.py` / `vampire_hostiles.py` /
  `hunter_ai.py` / `economy.py` / `personas.py` (D33's Cadence town simulation:
  NEEDS meters, the per-tick behavior loop + NPC seeding, zone-confined
  pathfinding, D34 Vampire predation, hunter escalation, the scrip vendor
  economy, and personality-trait flavor — `cadence.py` re-exports the split
  modules' historical private names), `hospital.py` (Town Clinic injury path),
  `verbs/` (SUPERS command handlers — `SUPERS_COMMANDS` merged by
  `commands.py`). Their JSON data lives at `supers/content/`
  (`origins.json`, `disciplines.json`, `appearance.json`, `items.json`,
  `relics.json`, `personas.json`, `bestiary/*.json`, `hostiles/*.json`,
  `combat_lexicon/**`); the Cadence town NPC roster lives at
  `content/npcs/*.json` instead (world content, like `content/maps/*.json`,
  not per-character catalog data). Content authoring index:
  `docs/CONTENT_AUTHORING.md` (+ `docs/templates/`).
- **Repo root — the shared core, NOT yet fully decomposed into engine vs. SUPERS**:
  `server.py` (entry point, the `Game` class + tick loop; calls
  `supers.bootstrap.register_all_hooks` at import), `world.py`
  (`GameObject`/`Room`/`Item`/`Character` — engine-ish core fields live here;
  game composition runs via `engine.hooks.attach_character` at the end of
  `Character.__init__`), `commands.py` (thin merge of `ENGINE_COMMANDS` +
  `SUPERS_COMMANDS` into the live `COMMANDS` dispatch table), `help_topics.py`
  (`HELP_TOPICS` / `HELP_CATEGORIES`; injected into `engine.hooks` at boot),
  `command_support.py` (helpers shared by both verb packages), `chargen.py`
  (multi-step new-character prompts — registered via `engine.hooks.set_chargen`,
  not imported by `connection.py`), `persistence.py` (SQLite save/load —
  tables/rooms/items here; blob codec via `engine.hooks`), `maps.py` (the
  world-map loader; generic in shape, but the map content it loads,
  `content/maps/*.json`, is SUPERS setting content). Deep split / two-repo
  purity roadmap: `docs/plans/two_repo_purity.md` (consumer guide:
  `docs/ENGINE_CONSUMER.md`). Until remotes split, "just the engine" means
  `engine/` plus these shared root files, minus everything under `supers/`.
- Tests: `smoke_test.py` (repo root — exercises both sides together)
- Standalone dev tools, not part of the engine (never imported by `server.py`):
  `tools/map_editor/` — a local web app for authoring `content/maps/*.json`; see its
  own module docstrings, not this file, for how it's built; `tools/apply_pr_fix.py`
  — checkout a bug-fixer PR so Docker's bind-mount + `watch_and_run` copyover
  picks it up quickly
- Design source of truth: `docs/SYSTEMS_DESIGN.md`
- Build status / next-up: `HANDOFF.md`
- Two-repo purity (public Riftforge / private SUPERS): `docs/plans/two_repo_purity.md`
- Engine architecture rationale (frozen, not a live tracker): `docs/ENGINE_ROADMAP.md`
