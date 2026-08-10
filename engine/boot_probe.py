"""boot_probe.py -- prove SUPERS can register hooks and construct Game().

Used by CI (``tools/boot_probe.py``) and auto-deploy overlay rollback so
import-time validation / facade regressions fail before live picks up bad
files. Runs in a fresh process with a throwaway SQLite DB.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback


def boot_probe_enabled():
    """True unless AUTO_DEPLOY_BOOT_PROBE / RIFTFORGE_BOOT_PROBE is off."""
    for key in ("AUTO_DEPLOY_BOOT_PROBE", "RIFTFORGE_BOOT_PROBE"):
        raw = (os.environ.get(key) or "").strip().lower()
        if raw in ("0", "false", "no", "off"):
            return False
    return True


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


def run_boot_probe_subprocess(*, cwd=None, timeout=180.0, game_name="supers"):
    """Spawn ``tools/boot_probe.py`` in a clean process (overlay safety).

    Returns ``(ok: bool, detail: str)``.
    """
    import subprocess

    root = cwd or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "tools", "boot_probe.py")
    env = os.environ.copy()
    if game_name:
        env["RIFTFORGE_GAME"] = game_name
    try:
        result = subprocess.run(
            [sys.executable, script],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=float(timeout),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"boot probe timed out after {timeout:.0f}s"
    except OSError as exc:
        return False, f"boot probe spawn failed: {exc}"

    chunks = []
    if result.stdout:
        chunks.append(result.stdout.strip())
    if result.stderr:
        chunks.append(result.stderr.strip())
    detail = "\n".join(chunks).strip() or f"exit {result.returncode}"
    return result.returncode == 0, detail[:2000]
