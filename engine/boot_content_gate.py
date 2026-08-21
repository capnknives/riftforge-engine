"""boot_content_gate.py -- fail fast on bad on-disk content before world load.

Runs the registered game hook (SUPERS validates catalogs) so ``Game()``
records a clear ``boot_failure`` stamp instead of dying deep inside
``build_world`` / ``maps`` with an opaque traceback.

Env: ``RIFTFORGE_BOOT_CONTENT_GATE=0`` disables the gate (emergency only).
"""

from __future__ import annotations

import json
import os


class BootContentGateError(Exception):
    """Malformed catalog or content JSON before world construction."""

    def __init__(self, path, message, *, kind_id=None):
        self.path = path
        self.kind_id = kind_id
        rel = path
        try:
            rel = os.path.relpath(path, os.getcwd())
        except ValueError:
            pass
        detail = f"{rel}: {message}"
        if kind_id:
            detail = f"{rel} ({kind_id}): {message}"
        super().__init__(detail)
        self.message = message


def boot_content_gate_enabled():
    raw = (os.environ.get("RIFTFORGE_BOOT_CONTENT_GATE") or "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def validate_json_syntax(path):
    """Parse one JSON file; raise BootContentGateError on failure."""
    try:
        with open(path, encoding="utf-8") as handle:
            json.load(handle)
    except OSError as exc:
        raise BootContentGateError(path, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise BootContentGateError(
            path, f"JSON syntax error line {exc.lineno}: {exc.msg}",
        ) from exc


def validate_json_tree(root_dir, *, label=None):
    """Syntax-check every ``*.json`` under *root_dir* (shallow + recursive)."""
    if not os.path.isdir(root_dir):
        return
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                validate_json_syntax(path)
            except BootContentGateError as exc:
                if label:
                    exc.args = (f"{label}: {exc.args[0]}",)
                raise


def run_boot_content_gate():
    """Invoke the registered game validator, if any."""
    if not boot_content_gate_enabled():
        return
    from engine import hooks

    fn = hooks.boot_content_gate()
    if fn is not None:
        fn()
