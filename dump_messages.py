"""Diagnostic: capture incoming Meshtastic packets and dump everything available.

Unlike dump_nodes.py (which reads a static node DB), messages arrive as events,
so this script subscribes to the receive feed and listens for a while.

Run on the Pi:
    uv run python dump_messages.py            # listen 180 s (default)
    uv run python dump_messages.py 600        # listen 600 s
    MESH_DEVICE=/dev/ttyACM0 uv run python dump_messages.py

For each packet it prints:
  * a CLASSIFICATION banner (TEXT message vs. telemetry/position/etc vs.
    an encrypted packet the device could not decrypt)
  * the full raw packet as JSON (bytes shown as hex)
  * which top-level / decoded fields were present
At the end it prints a summary table grouped by classification and portnum.
"""

import json
import os
import sys
import time
from collections import Counter

from pubsub import pub
from meshtastic.serial_interface import SerialInterface

DEVICE = os.environ.get("MESH_DEVICE", "/dev/ttyACM0")

# How long to listen, in seconds. First CLI arg overrides.
LISTEN_SECONDS = 180
if len(sys.argv) > 1:
    try:
        LISTEN_SECONDS = int(sys.argv[1])
    except ValueError:
        print(f"Ignoring bad duration {sys.argv[1]!r}; using {LISTEN_SECONDS}s")


def safe(v):
    """Recursively make a value JSON-serialisable (bytes -> hex)."""
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, dict):
        return {k: safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [safe(x) for x in v]
    return v


def classify(packet):
    """Return (category, portnum) for a received packet.

    Categories:
      TEXT      - a real, human-readable chat message
      ENCRYPTED - device received it but could not decrypt (no `decoded`)
      <PORT>    - any other decoded app packet (telemetry, position, ...)
    """
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        # No decoded payload: either encrypted-and-undecryptable or malformed.
        if packet.get("encrypted") is not None:
            return "ENCRYPTED", None
        return "UNDECODED", None

    portnum = (
        decoded.get("portnum")
        or decoded.get("portNum")
        or "UNKNOWN_PORT"
    )
    portnum = str(portnum)

    has_text = bool(decoded.get("text"))
    if not has_text and "TEXT_MESSAGE" in portnum.upper():
        # portnum says text but payload not decoded into `text`
        payload = decoded.get("payload")
        has_text = isinstance(payload, (bytes, bytearray, str)) and bool(payload)

    if has_text:
        return "TEXT", portnum
    return portnum, portnum


# ---- collected state -------------------------------------------------------
packets = []          # list of (index, category, portnum, packet)
counter = Counter()   # category -> count
port_counter = Counter()
start_time = time.time()


def describe_fields(packet):
    """Short list of which interesting fields are populated."""
    top = [k for k in packet.keys()]
    decoded = packet.get("decoded")
    dec = list(decoded.keys()) if isinstance(decoded, dict) else []
    return top, dec


def on_receive(packet, interface=None):
    if not isinstance(packet, dict):
        print(f"\n!! non-dict packet: {type(packet)} -> {packet!r}")
        return

    index = len(packets) + 1
    category, portnum = classify(packet)
    packets.append((index, category, portnum, packet))
    counter[category] += 1
    if portnum:
        port_counter[portnum] += 1

    elapsed = time.time() - start_time
    top, dec = describe_fields(packet)

    print("\n" + "=" * 70)
    print(f"#{index}  [{category}]  +{elapsed:6.1f}s")
    print("=" * 70)
    print(f"from        : {packet.get('fromId')!r}  (raw from={packet.get('from')!r})")
    print(f"to          : {packet.get('toId')!r}  (raw to={packet.get('to')!r})")
    print(f"portnum     : {portnum!r}")
    print(f"channel     : index={packet.get('channelIndex')!r} "
          f"channel={packet.get('channel')!r}")
    print(f"rxTime      : {packet.get('rxTime')!r}")
    print(f"rxRssi/rxSnr: {packet.get('rxRssi')!r} / {packet.get('rxSnr')!r}")
    print(f"hopLimit    : {packet.get('hopLimit')!r}  hopStart={packet.get('hopStart')!r}")
    print(f"top-level keys : {top}")
    print(f"decoded keys   : {dec}")
    print("--- full packet ---")
    print(json.dumps(safe(packet), indent=2, default=str))


def on_connection(interface, topic=pub.AUTO_TOPIC):
    print("** device connected **")


def main():
    print(f"# Listening on {DEVICE} for {LISTEN_SECONDS}s ...")
    print("# (real text messages, telemetry/position/etc., and encrypted packets)")

    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")

    iface = SerialInterface(devPath=DEVICE)
    try:
        time.sleep(LISTEN_SECONDS)
    except KeyboardInterrupt:
        print("\n# interrupted")
    finally:
        iface.close()

    print("\n\n" + "#" * 70)
    print(f"# SUMMARY  ({len(packets)} packets in {time.time() - start_time:.0f}s)")
    print("#" * 70)
    if not packets:
        print("# No packets received. Nothing was on the air, or no traffic decoded.")
        return

    print("\nBy classification:")
    for category, count in counter.most_common():
        print(f"  {category:<22} {count}")

    print("\nBy portnum:")
    for port, count in port_counter.most_common():
        print(f"  {port:<22} {count}")

    real = counter.get("TEXT", 0)
    encrypted = counter.get("ENCRYPTED", 0) + counter.get("UNDECODED", 0)
    other = len(packets) - real - encrypted
    print(f"\nReal text messages : {real}")
    print(f"Encrypted/undecoded: {encrypted}")
    print(f"Other/status       : {other}")


if __name__ == "__main__":
    main()
