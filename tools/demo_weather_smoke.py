"""demo_weather_smoke.py -- targeted basegame CONUS weather check."""

from __future__ import annotations

import os
import sys


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)
    os.environ["RIFTFORGE_GAME"] = "basegame"

    import game_select
    game_select._reset_for_tests()
    import server as server_mod
    from engine.systems import regional_weather as wx

    game = server_mod.Game(db_path=":memory:")
    room = game.rooms["NB00008"]
    snap = wx.weather_for_room(room, game)
    assert snap.get("region") == "great_plains", snap
    lines = wx.report_lines(game, room=room)
    text = "\n".join(lines) if isinstance(lines, list) else str(lines)
    assert isinstance(text, str) and text, text
    game.db.close()
    print("demo_weather_smoke_ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
