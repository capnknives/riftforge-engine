"""Runtime GM toggles for the two lag threads (persist writer + Cadence planner).

These flags normally live in live ``.env`` and apply on game-child spawn
via :func:`engine.env_file.apply_repo_env`. Staff also need to flip them
**in-game** without a compose recreate (hard rule 19). This module:

* reads/writes ``.lag_thread_override`` (same sidecar idea as
  ``.diag_enabled_override``) so a copyover / game-only restart keeps
  the GM choice
* starts or stops the threads on the running process
* builds the ``gm lag`` diagnostic sheet

WAL journal mode is **not** togglable here. It is a file-level SQLite
property; flipping it from a live command risks the same DELETE+two-
connection corruption class the writer fail-softs to avoid. The sheet
shows WAL as a read-only diagnostic.

Engine-only -- no ``supers`` imports.
"""

from __future__ import annotations

import os


OVERRIDE_NAME = ".lag_thread_override"
WRITER_ENV = "RIFTFORGE_PERSIST_BACKGROUND_WRITER"
PLANNER_ENV = "RIFTFORGE_CADENCE_PLANNER_THREAD"
# Short drain so ``gm lag writer off`` does not park the heartbeat for
# the 120s process-exit writer timeout. Queue is latest-wins size 1.
GM_TOGGLE_DRAIN_S = 5.0

_TRUE = ("1", "on", "true", "yes")
_FALSE = ("0", "off", "false", "no")


def override_path(root):
    """Absolute path of the GM sidecar under ``root`` (checkout / report dir)."""
    return os.path.join(root or ".", OVERRIDE_NAME)


def _parse_bool(raw):
    """Return True/False for a flag token, or None when the value is junk."""
    text = str(raw or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def read_override(root):
    """Return ``{"writer": bool|None, "planner": bool|None}``.

    None means "this key is not in the sidecar -- inherit ``.env`` /
    process env." Missing file → both None.
    """
    out = {"writer": None, "planner": None}
    path = override_path(root)
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return out
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        key = name.strip().lower()
        parsed = _parse_bool(value)
        if parsed is None:
            continue
        if key in ("writer", "persist", "persist_writer"):
            out["writer"] = parsed
        elif key in ("planner", "cadence_planner"):
            out["planner"] = parsed
    return out


def write_override(root, *, writer=None, planner=None):
    """Merge one or both keys into the sidecar (other key kept).

    Pass ``None`` to leave that key as it already is in the file.
    """
    current = read_override(root)
    if writer is not None:
        current["writer"] = bool(writer)
    if planner is not None:
        current["planner"] = bool(planner)
    path = override_path(root)
    lines = [
        "# gm lag override -- gitignored sidecar; survives copyover / deploy stash",
        "# writer = persist background SQLite apply thread",
        "# planner = Cadence lifestyle planner thread",
    ]
    if current["writer"] is not None:
        lines.append("writer=" + ("on" if current["writer"] else "off"))
    if current["planner"] is not None:
        lines.append("planner=" + ("on" if current["planner"] else "off"))
    text = "\n".join(lines) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def clear_override(root):
    """Delete the sidecar so the next apply inherits ``.env`` again."""
    path = override_path(root)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def apply_override(root):
    """Overlay sidecar values onto ``os.environ`` (no-op when file missing).

    Call this after :func:`engine.env_file.apply_repo_env` (or at
    ``Game.__init__`` after ``report_dir`` is known) and **before**
    boot starts the writer / planner threads.
    """
    flags = read_override(root)
    if flags["writer"] is not None:
        os.environ[WRITER_ENV] = "1" if flags["writer"] else "0"
    if flags["planner"] is not None:
        os.environ[PLANNER_ENV] = "1" if flags["planner"] else "0"


def set_env_flag(name, on):
    """Set a truthy env flag to ``1`` or ``0`` (helpers read this live)."""
    os.environ[name] = "1" if on else "0"


def _event_loop():
    """Running tick loop, or a new loop when called from a smoke ``Game()``."""
    import asyncio

    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


def wal_is_safe(game):
    """True when a second SQLite connection is safe (WAL or ``:memory:``)."""
    db_path = getattr(game, "db_path", None) or ""
    if db_path == ":memory:":
        return True
    conn = getattr(game, "db", None)
    if conn is None:
        return False
    from engine import sqlite_wal

    return sqlite_wal.journal_mode(conn) == "wal"


def journal_mode_label(game):
    """Lowercase PRAGMA journal_mode, or a short fallback."""
    db_path = getattr(game, "db_path", None) or ""
    if db_path == ":memory:":
        return "memory"
    conn = getattr(game, "db", None)
    if conn is None:
        return "n/a"
    from engine import sqlite_wal

    return sqlite_wal.journal_mode(conn) or "unknown"


def override_root(game):
    """Directory that holds the sidecar (same as report logs / ``.env``)."""
    return getattr(game, "report_dir", None) or "."


def sync_threads_to_env(game, *, drain_timeout=GM_TOGGLE_DRAIN_S):
    """Start or stop threads so they match the current env flags.

    Returns a list of staff-facing note strings (empty when already
    matched). The asyncio loop is blocked during drain/join -- that is
    safe for a rare GM toggle because ticks cannot start a competing
    save until this command returns.
    """
    notes = []
    loop = _event_loop()
    from engine import persistence as persist_mod
    from engine import persistence_writer as writer_mod
    from engine import cadence_planner as planner_mod

    want_writer = persist_mod.persist_background_writer_enabled()
    have_writer = writer_mod.writer_started()
    if want_writer and not have_writer:
        if not wal_is_safe(game):
            notes.append(
                "Writer env is ON but WAL is not active "
                f"(journal={journal_mode_label(game)}) -- thread not "
                "started. Live Linux needs RIFTFORGE_SQLITE_JOURNAL=WAL; "
                "staging Docker stays DELETE on purpose."
            )
        else:
            writer_mod.start_persistence_writer(
                getattr(game, "db_path", None) or "riftforge.db",
                loop=loop,
            )
            notes.append("Persist writer thread started.")
    elif (not want_writer) and have_writer:
        writer_mod.shutdown_persistence_writer(timeout=float(drain_timeout))
        notes.append("Persist writer thread stopped (saves apply on the heartbeat).")

    want_planner = planner_mod.cadence_planner_enabled()
    have_planner = planner_mod.planner_started()
    if want_planner and not have_planner:
        planner_mod.start_cadence_planner(loop=loop)
        notes.append("Cadence planner thread started.")
    elif (not want_planner) and have_planner:
        planner_mod.shutdown_cadence_planner(
            timeout=min(float(drain_timeout), 3.0),
        )
        notes.append("Cadence planner thread stopped (LOD lifestyle stays on-loop).")
    return notes


def set_writer(game, on):
    """GM: persist writer on/off. Returns (ok, notes)."""
    if on and not wal_is_safe(game):
        return False, [
            "Cannot turn the persist writer on without WAL "
            f"(journal={journal_mode_label(game)}). Leave it off on "
            "Windows staging; live Linux .env already sets WAL.",
        ]
    set_env_flag(WRITER_ENV, on)
    path = write_override(override_root(game), writer=bool(on))
    notes = sync_threads_to_env(game)
    notes.append(f"Wrote {path}")
    return True, notes


def set_planner(game, on):
    """GM: Cadence planner on/off. Returns (ok, notes)."""
    set_env_flag(PLANNER_ENV, on)
    path = write_override(override_root(game), planner=bool(on))
    notes = sync_threads_to_env(game)
    notes.append(f"Wrote {path}")
    return True, notes


def reset_to_env(game):
    """Drop the sidecar, re-read ``.env``, and match threads. Returns notes."""
    from engine import env_file

    root = override_root(game)
    cleared = clear_override(root)
    env_file.apply_repo_env(root)
    notes = []
    if cleared:
        notes.append(f"Removed {override_path(root)} -- .env wins again.")
    else:
        notes.append("No sidecar was present; re-read .env anyway.")
    notes.extend(sync_threads_to_env(game))
    return notes


def _flag_triple(env_on, thread_on, override_val):
    """Compact 'on/off' plus whether env, thread, and sidecar agree."""
    env_txt = "on" if env_on else "off"
    thread_txt = "running" if thread_on else "stopped"
    if override_val is None:
        src = "from .env"
    else:
        src = "GM override " + ("on" if override_val else "off")
    return f"{env_txt} ({thread_txt}, {src})"


def tick_summary_line(game):
    """One-liner for ``gm tick`` pointing at the full ``gm lag`` sheet."""
    from engine import persistence as persist_mod
    from engine import persistence_writer as writer_mod
    from engine import cadence_planner as planner_mod

    writer = "on" if persist_mod.persist_background_writer_ready() else "off"
    planner = (
        "on"
        if (
            planner_mod.cadence_planner_enabled()
            and planner_mod.planner_started()
        )
        else "off"
    )
    return f"Lag threads: writer={writer} planner={planner}  (gm lag)"


def status_lines(game):
    """Staff sheet body: what the flags do + live diagnostics."""
    from engine import persistence as persist_mod
    from engine import persistence_writer as writer_mod
    from engine import cadence_planner as planner_mod
    from engine import style

    flags = read_override(override_root(game))
    last = getattr(game, "_cadence_last_pass", None) or {}
    phases = last.get("phases") or {}
    autosave = getattr(game, "_last_autosave_stats", None) or {}
    wal_env = (os.environ.get("RIFTFORGE_SQLITE_JOURNAL") or "").strip() or "<unset>"
    max_actors = planner_mod.cadence_planner_max_actors()

    writer_env = persist_mod.persist_background_writer_enabled()
    planner_env = planner_mod.cadence_planner_enabled()
    writer_thread = writer_mod.writer_started()
    planner_thread = planner_mod.planner_started()

    lines = [
        style.paint("muted", "(in-process -- no Docker recreate)"),
        "These are NOT gm cadence knobs (per/step/hub_lod). Those only",
        "throttle who acts. These two threads move work off the heartbeat.",
        "",
        "WAL journal (read-only here -- do not flip from a command):",
        f"  file mode={journal_mode_label(game)}  env={wal_env}",
        "  Live Linux wants WAL so the persist writer can use a second",
        "  SQLite connection. Staging Docker stays DELETE (bind-mount",
        "  WAL has corrupted the DB before). Writer will not start",
        "  without WAL on an on-disk file.",
        "",
        "Persist writer -- RIFTFORGE_PERSIST_BACKGROUND_WRITER",
        "  What it does: autosave still *collects* on the tick; SQL apply",
        "  + checkpoint run on one background thread so look/walk are not",
        "  stuck behind fsync. Off = apply runs on the heartbeat (hitch",
        "  during save is expected).",
        f"  Status: {_flag_triple(writer_env, writer_thread, flags['writer'])}",
    ]
    if writer_thread:
        pending = "yes" if writer_mod.writer_busy() else "no"
        lines.append(f"  Queue pending: {pending}")
    if autosave:
        bits = [f"save={autosave.get('save_ms')}ms"]
        if autosave.get("collect_ms") is not None:
            bits.append(f"collect={autosave.get('collect_ms')}")
        if autosave.get("apply_ms") is not None:
            bits.append(f"apply={autosave.get('apply_ms')}")
        if autosave.get("writer_enqueued"):
            bits.append("handed_to_writer=yes")
        else:
            bits.append("handed_to_writer=no")
        if autosave.get("writer_total_ms") is not None:
            bits.append(f"writer_ms={autosave.get('writer_total_ms')}")
        if autosave.get("writer_error"):
            bits.append(f"writer_err={autosave.get('writer_error')}")
        lines.append("  Last autosave: " + " ".join(str(b) for b in bits))
    else:
        lines.append("  Last autosave: (none yet this boot)")

    lines.extend([
        "",
        "Cadence planner -- RIFTFORGE_CADENCE_PLANNER_THREAD",
        "  What it does: unwatched / LOD town NPCs and Echoes get lifestyle",
        "  decisions (sleep, wander) computed on a read-only thread.",
        "  Watched rooms, combat, and jobs stay on the main loop either",
        "  way. Cap is RIFTFORGE_CADENCE_PLANNER_MAX_ACTORS "
        f"(now {max_actors}; 0 = no cap).",
        f"  Status: {_flag_triple(planner_env, planner_thread, flags['planner'])}",
    ])
    if planner_thread:
        pending = "yes" if planner_mod.planner_busy() else "no"
        lines.append(f"  Queue pending: {pending}")

    setup_ms = phases.get("setup_ms")
    dogs_ms = phases.get("sanctuary_dogs_ms")
    used = last.get("budget_used")
    limit = last.get("budget_limit")
    wall = last.get("wall_ms")
    cad_bits = []
    if used is not None and limit is not None:
        cad_bits.append(f"slots={used}/{limit}")
    if setup_ms is not None:
        cad_bits.append(f"setup={setup_ms}ms")
    if dogs_ms is not None:
        cad_bits.append(f"sanctuary_dogs={dogs_ms}ms")
    if wall is not None:
        cad_bits.append(f"wall={wall}ms")
    if cad_bits:
        lines.append("  Last Cadence pass: " + " ".join(cad_bits))
    else:
        lines.append("  Last Cadence pass: (none yet -- wait one tick)")

    lines.extend([
        "",
        "See also: gm tick (heartbeat)  |  gm cadence (scale/LOD)  |  "
        "gm diaglog (NDJSON, different writers)",
        "Usage: gm lag | gm lag writer on|off | gm lag planner on|off "
        "| gm lag reset",
        "reset drops the sidecar so production .env wins on this process.",
    ])
    return lines
