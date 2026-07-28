"""work.py -- clock in at room job sites (demo)."""

JOB_ID = "tornado_hunter"


def cmd_work(character, args, game):
    """Clock in at a room job site (e.g. Storm Watch Office)."""
    room = getattr(character, "location", None)
    jobs = tuple(getattr(room, "jobs", None) or ())
    if not jobs:
        character.session.send("There is no work desk here.")
        return
    raw = (args or "").strip().lower()
    if raw.startswith("as "):
        raw = raw[3:].strip()
    job = raw if raw in jobs else jobs[0]
    character.job = job
    character.on_duty = True
    character.session.send(f"You clock in as {job}.")
