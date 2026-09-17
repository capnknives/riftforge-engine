"""boot_probe.py -- prove SUPERS can register hooks and construct Game().

Used by GitHub ``boot-probe``, auto-deploy overlay rollback, and
``tools/local_ci.py --full``. Not a targeted smoke: constructing ``Game()``
stitches the world (Cadence, lodging, maps) and is too slow for pre-push.
Targeted smokes stub the few attributes they need instead of calling this.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback


# Live Game() on the droplet often takes 3–5 minutes (copyover 264s was
# measured 2026-09-11). The old 180s subprocess cap then abort-held good
# merges and git-reset live back to the previous SHA. Ten minutes is the
# floor that still leaves crash recovery as the backstop for a hung boot.
DEFAULT_BOOT_PROBE_TIMEOUT_S = 600.0
MIN_BOOT_PROBE_TIMEOUT_S = 60.0


def boot_probe_enabled():
    """True unless AUTO_DEPLOY_BOOT_PROBE / RIFTFORGE_BOOT_PROBE is off."""
    for key in ("AUTO_DEPLOY_BOOT_PROBE", "RIFTFORGE_BOOT_PROBE"):
        raw = (os.environ.get(key) or "").strip().lower()
        if raw in ("0", "false", "no", "off"):
            return False
    return True


def boot_probe_timeout_seconds(explicit=None):
    """Seconds the watcher waits for a throwaway ``Game()`` subprocess.

    ``explicit`` wins (tests). Else ``RIFTFORGE_BOOT_PROBE_TIMEOUT`` or
    ``AUTO_DEPLOY_BOOT_PROBE_TIMEOUT`` from env (``.env`` via apply_repo_env).
    """
    if explicit is not None:
        try:
            return max(MIN_BOOT_PROBE_TIMEOUT_S, float(explicit))
        except (TypeError, ValueError):
            pass
    for key in ("RIFTFORGE_BOOT_PROBE_TIMEOUT", "AUTO_DEPLOY_BOOT_PROBE_TIMEOUT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            return max(MIN_BOOT_PROBE_TIMEOUT_S, float(raw))
        except ValueError:
            continue
    return DEFAULT_BOOT_PROBE_TIMEOUT_S


def boot_probe_timed_out(detail):
    """True when the subprocess was killed for wall-clock, not a crash."""
    text = (detail or "").lower()
    return "timed out after" in text


def run_boot_probe(*, db_path=None, game_name="supers"):
    """Register hooks and construct ``Game()`` once.

    Returns ``(ok: bool, detail: str)``. Importing ``server`` runs
    ``register_all_hooks()`` at module load (same path as production).
    """
    cleanup_db = False
    if not db_path:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cleanup_db = True

    prev_db = os.environ.get("RIFTFORGE_DB")
    prev_game = os.environ.get("RIFTFORGE_GAME")
    os.environ["RIFTFORGE_DB"] = db_path
    if game_name:
        os.environ["RIFTFORGE_GAME"] = game_name

    try:
        # Fresh interpreter only -- caller should use subprocess for overlays.
        import server  # noqa: F401 -- register_all_hooks at import
        from server import Game

        game = Game(db_path=db_path)
        try:
            if getattr(game, "db", None) is not None:
                game.db.close()
        finally:
            del game
        return True, "boot ok"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if os.environ.get("RIFTFORGE_BOOT_PROBE_VERBOSE", "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            detail = detail + "\n" + traceback.format_exc()
        return False, detail[:2000]
    finally:
        if prev_db is None:
            os.environ.pop("RIFTFORGE_DB", None)
        else:
            os.environ["RIFTFORGE_DB"] = prev_db
        if prev_game is None:
            os.environ.pop("RIFTFORGE_GAME", None)
        else:
            os.environ["RIFTFORGE_GAME"] = prev_game
        if cleanup_db:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(db_path + suffix)
                except OSError:
                    pass


def run_boot_probe_subprocess(*, cwd=None, timeout=None, game_name="supers"):
    """Spawn ``tools/boot_probe.py`` in a clean process (overlay safety).

    Returns ``(ok: bool, detail: str)``. ``timeout=None`` uses
    :func:`boot_probe_timeout_seconds` (default 600s, env override).
    A timeout is *not* a Game() crash -- callers should not abort-hold.
    """
    import subprocess

    root = cwd or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "tools", "boot_probe.py")
    env = os.environ.copy()
    if game_name:
        env["RIFTFORGE_GAME"] = game_name
    seconds = boot_probe_timeout_seconds(timeout)
    try:
        result = subprocess.run(
            [sys.executable, script],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=float(seconds),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"boot probe timed out after {seconds:.0f}s"
    except OSError as exc:
        return False, f"boot probe spawn failed: {exc}"

    chunks = []
    if result.stdout:
        chunks.append(result.stdout.strip())
    if result.stderr:
        chunks.append(result.stderr.strip())
    detail = "\n".join(chunks).strip() or f"exit {result.returncode}"
    return result.returncode == 0, detail[:2000]
