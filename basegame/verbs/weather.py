"""weather.py -- player weather / forecast verbs for basegame."""

from engine.systems import regional_weather as weather_mod


def cmd_weather(character, args, game):
    """Show regional weather for the character's current room."""
    character.session.send(weather_mod.player_weather_report(character, game))


def cmd_forecast(character, args, game):
    """Show a short regional forecast."""
    character.session.send(weather_mod.player_forecast_report(character, game))
