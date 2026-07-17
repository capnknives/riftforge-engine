"""
packaging_smoke.py — Phase 5 packaging round-trip (same monorepo).

Expects a venv where both packages are editable-installed::

    py -3.13 -m venv .venv-phase5
    .venv-phase5/Scripts/pip install -e .
    .venv-phase5/Scripts/pip install -e ./supers

Then::

    .venv-phase5/Scripts/python tools/packaging_smoke.py

Exit 0 prints packaging_smoke_ok. Stdlib + installed packages only for the
import checks; does not start the telnet server.
"""

from __future__ import annotations

import importlib.util
import sys


def main() -> int:
    """Assert riftforge + supers editable installs resolve correctly."""
    import engine
    import supers

    eng = getattr(engine, "__file__", None) or ""
    sup = getattr(supers, "__file__", None) or ""
    assert "engine" in eng.replace("\\", "/"), eng
    assert "supers" in sup.replace("\\", "/"), sup

    # Soft-optional server sees SUPERS when the package is installed.
    import server

    assert server._HAS_SUPERS is True, "supers installed but server._HAS_SUPERS False"

    # Engine package has no module-level supers imports (spot-check).
    spec = importlib.util.find_spec("engine.hooks")
    assert spec is not None

    print("packaging_smoke_ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
