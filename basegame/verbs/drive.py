"""drive.py -- basegame boarded cart demo (engine/systems/vehicles.py)."""

from engine.systems import vehicles as vehicles_mod


def cmd_board(character, args, game):
    """Board a parked vehicle by name (e.g. board cart)."""
    query = (args or "").strip()
    if not query:
        session = getattr(character, "session", None)
        if session is not None:
            session.send("Board what? Try: board cart")
        return
    if not vehicles_mod.try_board(character, query, game):
        session = getattr(character, "session", None)
        if session is not None:
            session.send(f"There is no '{query}' to board here.")


def cmd_drive(character, args, game):
    """Drive the boarded vehicle one room-graph hop (driver only)."""
    session = getattr(character, "session", None)
    direction = (args or "").strip().lower()
    if not direction:
        if session is not None:
            session.send("Drive which direction?")
        return
    vid = getattr(character, "in_vehicle", None)
    if not vid:
        if session is not None:
            session.send("You're not in a vehicle.")
        return
    veh = vehicles_mod.vehicle_by_id(game, vid)
    if veh is None:
        if session is not None:
            session.send("Your vehicle is missing from the world.")
        return
    if vehicles_mod.drive_step(character, veh, direction, game):
        return
    if session is not None:
        session.send(f"You can't drive {direction} from here.")


def cmd_unboard(character, args, game):
    """Climb out of the current vehicle (alias: leave while aboard)."""
    session = getattr(character, "session", None)
    if not vehicles_mod.leave_vehicle(character, game):
        if session is not None:
            session.send("You're not in a vehicle.")
