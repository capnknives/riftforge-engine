"""content_validate.py -- tiny shared helpers for fail-loud content loaders.

Stdlib only: no JSON Schema package. Each catalog loader still owns its own
_validate() (or equivalent); this module just factors the repetitive checks
(required keys, enum membership, unique ids) so maps / items / personas /
relics / hostiles stay consistent.

No networking, no world model -- pure functions over plain dicts/lists.

Moved from supers/content_validate.py in the two-repo purity Stage 1
migration (docs/plans/two_repo_purity.md). supers/content_validate.py is now
a re-export facade so existing `from supers import content_validate` call
sites keep working unchanged.
"""


def require_keys(obj, keys, where):
    """Raise AssertionError if `obj` is missing any of `keys`.

    `where` is a human-readable location string for the error message
    (e.g. \"items.json: 'loaf_of_bread'\").
    """
    if not isinstance(obj, dict):
        raise AssertionError(f"{where}: expected a dict, got {type(obj).__name__}")
    for key in keys:
        if key not in obj:
            raise AssertionError(f"{where}: missing required field '{key}'")


def require_nonempty_str(obj, key, where):
    """Require obj[key] to be a non-empty string."""
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"{where}: '{key}' must be a non-empty string")


def require_number(obj, key, where):
    """Require obj[key] to be an int or float (not bool)."""
    value = obj.get(key)
    # bool is a subclass of int in Python -- reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"{where}: '{key}' must be a number")


def require_in(value, allowed, where, label="value"):
    """Require `value` to be a member of `allowed` (set/tuple/list)."""
    if value not in allowed:
        raise AssertionError(
            f"{where}: unknown {label} {value!r} -- must be one of "
            f"{sorted(allowed)}"
        )


def unique_ids(entries, where, id_key="id"):
    """Assert every entry has id_key and that those ids are unique."""
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise AssertionError(f"{where}: entry must be a dict")
        eid = entry.get(id_key)
        if not eid:
            raise AssertionError(f"{where}: entry missing '{id_key}'")
        if eid in seen:
            raise AssertionError(f"{where}: duplicate '{id_key}' {eid!r}")
        seen.add(eid)
    return seen
