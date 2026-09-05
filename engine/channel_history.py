"""
channel_history.py -- backward-compatible facade over ``engine.channels``.

New code should import ``engine.channels`` directly. This module keeps the
old import paths working for gateway, persistence, and ooc_channel.
"""

from engine.channels import (  # noqa: F401
    DEFAULT_RING_MAX,
    ChannelSpec,
    all_channels,
    append,
    entries,
    export_gateway_plain_line,
    export_gateway_snapshot,
    gateway_stitch_channels,
    get_channel,
    import_gateway_stitch_lines,
    init_game,
    is_empty,
    load_all,
    load_channel,
    register_channel,
    render_ooc_entry,
    replay_wiznet_entry,
    replace_ring_from_gateway_lines,
    ring,
    save_all,
    save_channel,
    send_empty_hint,
    send_replay_header,
    stitch_channel_for_command,
    sync_gateway_stitch_channels,
)

OOC_HISTORY_MAX = DEFAULT_RING_MAX
WIZNET_HISTORY_MAX = DEFAULT_RING_MAX
