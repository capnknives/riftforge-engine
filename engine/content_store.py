"""content_store.py -- shared helpers for file-backed editable catalogs.

Catalogs (jobs, personas, items, NPC rosters, ...) live as JSON under
content/ or supers/content/ -- NOT in SQLite. Characters/Echoes are the DB
half; authored data stays on disk so watch_and_run, the map editor, and git
share one pipeline (docs/CONTENT_AUTHORING.md invariant).

This module only knows how to atomically rewrite a JSON file and how to
validate snake_case ids. Each catalog module keeps its own schema + mutators
and calls save_json() after editing its in-memory table.

Moved from supers/content_store.py in the two-repo purity Stage 1 migration
(docs/plans/two_repo_purity.md) -- pure stdlib, zero SUPERS coupling, so it
belongs in engine/ as the shared catalog-loader foundation. supers/content_store.py
is now a re-export facade so existing `from supers import content_store` call
sites keep working unchanged.
"""

import json
import os
import re
import tempfile
import time

# Ids authored via GM add verbs / JSON keys: readable over telnet, roster-safe.
_SNAKE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def require_snake_id(value, *, what="id"):
    """Raise ValueError unless value is a snake_case id starting with a letter."""
    if not isinstance(value, str) or not _SNAKE_ID_RE.match(value):
        raise ValueError(
            f"{what} must be snake_case starting with a letter "
            f"(a-z, digits, underscore), not {value!r}."
        )
    return value


def load_json(path):
    """Read a UTF-8 JSON file (fail loud on bad JSON)."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, data):
    """Validate-free atomic rewrite of `data` to `path` as pretty JSON + LF.

    Temp file in the same directory + os.replace so a crash mid-write cannot
    leave a truncated catalog. newline='\\n' matches the map editor's Windows
    LF policy (.gitattributes).

    On Windows, os.replace can raise PermissionError when a scanner or a
    brief reader still holds the destination (smoke Game restarts rewriting
    vehicle_parking.json are a common hit). Retry a few times before
    failing loud.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".content_", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=4)
            handle.write("\n")
        # Retry replace: Windows AV / indexer can briefly lock `path`.
        last_err = None
        for attempt in range(8):
            try:
                os.replace(tmp_path, path)
                last_err = None
                break
            except PermissionError as err:
                last_err = err
                time.sleep(0.05 * (attempt + 1))
        if last_err is not None:
            raise last_err
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
