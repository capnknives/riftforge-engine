"""
prose_pool_loader.py -- generic JSON prose-pool directory loader (engine kernel).

Any content pack that keeps its flavor text as a folder of named JSON
files (one file per pool, or a sub-folder of files) needs the same three
small pieces of plumbing: figure out which directory to read from (with a
test-only env-var override so a smoke can point the loader at a scratch
tree without touching the real content), read+parse one file out of that
directory, and hot-reload the whole owning module when content changes on
disk. This module holds exactly those three pieces and nothing about what
the JSON actually contains -- callers own their own pool shape, their own
env-var name, and their own default directory.

No networking, no world model, no game-specific vocabulary -- stdlib only.
Design: docs/plans/engine_liquid_flavor.md (Wave 3 Part B).
"""

from __future__ import annotations

import importlib
import json
import os


def pool_dir(default_dir, *, env_var):
    """Resolve the directory to load pool files from.

    Checks `env_var` first (a smoke can set it to redirect every load at a
    scratch tree without touching the real content on disk) and falls back
    to `default_dir` when that variable is unset or empty.
    """
    override = os.environ.get(env_var)
    if override:
        return override
    return default_dir


def set_pool_dir_for_tests(env_var, path):
    """Point (or un-point) the `env_var` override at `path` (smokes only).

    Passing a falsy `path` removes the override entirely so a later call
    to `pool_dir` falls back to the caller's own default directory again.
    """
    if path:
        os.environ[env_var] = path
    else:
        os.environ.pop(env_var, None)


def load_pool_json(pool_dir, *parts):
    """Read and parse one JSON file under `pool_dir`.

    `parts` are joined onto `pool_dir` the same way `os.path.join` joins
    any path segments, so a caller can pass a single filename or a nested
    sub-folder path (e.g. a sub-pool kept in its own folder). Lets a
    malformed or missing file raise its natural exception straight out of
    the caller's import -- pool content is meant to fail loud at boot, not
    the first time something tries to use it mid-play.
    """
    path = os.path.join(pool_dir, *parts)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def reload_pool_module(module_name):
    """Re-execute `module_name` from scratch (content hot-reload).

    A thin, generic wrapper around `importlib.reload` keyed by module
    name/string rather than a live module object, so the caller does not
    need to import itself to call this. Re-running the module's top-level
    code is what actually re-reads every pool file from disk.
    """
    module = importlib.import_module(module_name)
    importlib.reload(module)
