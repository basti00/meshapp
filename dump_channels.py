"""Diagnostic: dump the device's channel configuration.

Channel names / PSKs / roles live only on the device (localNode.channels),
not in the packet stream, so this reads them straight off the radio. Run on
the Pi to capture the exact shape before we build a `channels` table around it.

Run on the Pi:
    uv run python dump_channels.py
    MESH_DEVICE=/dev/ttyACM0 uv run python dump_channels.py

For each configured channel it prints:
  * index, role (DISABLED/PRIMARY/SECONDARY) and name
  * the PSK as hex plus its length in bytes (0 = none, 1 = default key,
    16 = AES128, 32 = AES256)
  * uplink/downlink flags and any module settings
  * the full protobuf rendered to a dict (so we see every field verbatim)
"""

import base64
import json
import os
import time

from meshtastic.serial_interface import SerialInterface

DEVICE = os.environ.get("MESH_DEVICE", "/dev/ttyACM0")

ROLE_NAMES = {0: "DISABLED", 1: "PRIMARY", 2: "SECONDARY"}


def safe(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, dict):
        return {k: safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [safe(x) for x in v]
    return v


def channel_to_dict(channel):
    """Render a Channel protobuf to a plain dict, every field verbatim."""
    try:
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(channel, preserving_proto_field_name=True)
    except Exception as exc:  # pragma: no cover - diagnostic best-effort
        return {"_MessageToDict_failed": str(exc)}


def main():
    iface = SerialInterface(devPath=DEVICE)
    # Give the interface a moment to populate localNode.
    time.sleep(2)

    ln = getattr(iface, "localNode", None)
    channels = getattr(ln, "channels", None) if ln is not None else None

    if not channels:
        print("# No channels found on localNode. Is the device connected?")
        iface.close()
        return

    print(f"# {len(channels)} channel slots known to the device\n")

    for channel in channels:
        index = getattr(channel, "index", None)
        role = getattr(channel, "role", None)
        settings = getattr(channel, "settings", None)
        name = getattr(settings, "name", None) if settings is not None else None
        psk = getattr(settings, "psk", b"") if settings is not None else b""
        psk_bytes = bytes(psk) if psk else b""

        print(f"=== slot index={index}  role={ROLE_NAMES.get(role, role)} ===")
        print(f"name          : {name!r}")
        print(f"psk length    : {len(psk_bytes)} bytes")
        print(f"psk (hex)     : {psk_bytes.hex()}")
        print(f"psk (base64)  : {base64.b64encode(psk_bytes).decode('ascii') if psk_bytes else ''}")
        if settings is not None:
            print(f"uplink_enabled  : {getattr(settings, 'uplink_enabled', None)!r}")
            print(f"downlink_enabled: {getattr(settings, 'downlink_enabled', None)!r}")
            print(f"channel id      : {getattr(settings, 'id', None)!r}")
        print("--- full protobuf as dict ---")
        print(json.dumps(safe(channel_to_dict(channel)), indent=2, default=str))
        print()

    # The packet stream tags messages with a channel index (or, for the
    # primary channel, nothing -> our "unknown" bucket). Print the index/name
    # pairing so we can confirm the messages.channel_key -> channel mapping.
    print("=== index -> name map ===")
    for channel in channels:
        settings = getattr(channel, "settings", None)
        name = getattr(settings, "name", "") if settings is not None else ""
        role = getattr(channel, "role", None)
        if role == 0:  # DISABLED
            continue
        print(f"  {getattr(channel, 'index', '?')}: {name!r}  ({ROLE_NAMES.get(role, role)})")

    iface.close()


if __name__ == "__main__":
    main()
