import base64
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

DB_SCHEMA_VERSION = 10
DB_PATH = Path(__file__).with_name("meshapp.db")
DEVICE_PATH = os.environ.get("MESH_DEVICE", "/dev/ttyACM0")
DEFAULT_CHANNEL_INDEX = int(os.environ.get("MESH_CHANNEL", "0"))
AUTO_REFRESH_SECONDS = int(os.environ.get("MESH_AUTO_REFRESH", "10"))
LISTEN_RETRY_SECONDS = 5
LIVE_THRESHOLD_SECONDS = 120 * 60

PING_KEYWORDS = ("PING",)

app = Flask(__name__)


def _json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def _json_dumps(value):
    return json.dumps(value, default=_json_default, ensure_ascii=True)


def _safe_json_loads(value):
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return None


def _normalize_timestamp(value):
    if value is None:
        return int(time.time())
    try:
        value = int(value)
    except (TypeError, ValueError):
        return int(time.time())
    if value > 1_000_000_000_000:
        value = value // 1000
    return value


def _format_time(value):
    if not value:
        return "-"
    dt = datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_message_time(value):
    if not value:
        return "-"
    dt = datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone()
    today = datetime.now().astimezone().date()
    if dt.date() == today:
        return dt.strftime("%H:%M")
    return dt.strftime("%d.%m.%Y %H:%M")


def _is_live(value):
    if not value:
        return False
    try:
        return (time.time() - int(value)) <= LIVE_THRESHOLD_SECONDS
    except (TypeError, ValueError):
        return False


def _join_metrics(parts):
    rendered = [p for p in parts if p]
    return " · ".join(rendered) if rendered else None


def _battery_summary(node):
    parts = []
    if node.get("battery_voltage") is not None:
        parts.append(f"{_format_value(node['battery_voltage'])} V")
    if node.get("battery_level") is not None:
        parts.append(f"{_format_value(node['battery_level'])} %")
    return _join_metrics(parts)


def _battery_info(level):
    if level is None:
        return None
    try:
        lvl = int(round(float(level)))
    except (TypeError, ValueError):
        return None
    lvl = max(0, min(lvl, 100))
    if lvl <= 20:
        css_class = "battery--low"
    elif lvl <= 50:
        css_class = "battery--medium"
    else:
        css_class = "battery--high"
    return {"level": lvl, "css_class": css_class}


def _node_subtitle(node):
    parts = []
    if node.get("last_seen"):
        parts.append(f"Last seen {_format_message_time(node['last_seen'])}")
    hops = node.get("last_hops")
    if hops is not None:
        label = "hop" if int(hops) == 1 else "hops"
        parts.append(f"{int(hops)} {label}")
    return " · ".join(parts) if parts else None


def _environment_summary(node):
    parts = []
    for key, unit in (("temperature", "°C"), ("humidity", "%hr"), ("pressure", "mbar")):
        formatted = _format_value(node.get(key))
        if formatted != "-":
            parts.append(f"{formatted} {unit}")
    return _join_metrics(parts)


def _format_value(value, digits=2):
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number or number in (float("inf"), float("-inf")):
        return "-"
    if number.is_integer():
        return str(int(number))
    return f"{number:.{digits}f}"


def _format_value_unit(value, unit, digits=2):
    formatted = _format_value(value, digits)
    if formatted == "-":
        return formatted
    return f"{formatted} {unit}"


def _local_day(value):
    if not value:
        return datetime.now().astimezone().strftime("%Y-%m-%d")
    dt = datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d")


def _coerce_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def _coerce_b64(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return str(value)


def _parse_node_num(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("!"):
        text = text[1:]
        base = 16
    elif text.startswith("0x"):
        text = text[2:]
        base = 16
    else:
        base = 16 if any(char in text for char in "abcdefABCDEF") else 10
    try:
        return int(text, base)
    except ValueError:
        return None


def _node_avatar_colors(node_id):
    node_num = _parse_node_num(node_id)
    if node_num is None:
        return {"avatar_bg": None, "avatar_fg": None}
    red = (node_num & 0xFF0000) >> 16
    green = (node_num & 0x00FF00) >> 8
    blue = node_num & 0x0000FF
    brightness = ((red * 0.299) + (green * 0.587) + (blue * 0.114)) / 255
    foreground = "#000000" if brightness > 0.5 else "#ffffff"
    background = f"#{red:02x}{green:02x}{blue:02x}"
    return {"avatar_bg": background, "avatar_fg": foreground}


def _db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# Node columns are grouped by section. Everything here is a value we either
# read straight off the mesh and promote to a column for cheap querying, or a
# value we computed/normalised ourselves. The *raw* meshtastic payloads are
# kept verbatim in the raw_*_json blobs at the end; the on-demand "sections"
# JSON shown in the UI is assembled from these (see _node_sections()).
NODE_COLUMNS = [
    # --- Identity (extracted from the raw NODEINFO user) ---
    ("short_name", "TEXT"),
    ("long_name", "TEXT"),
    ("hw_model", "TEXT"),
    ("role", "TEXT"),
    ("macaddr", "TEXT"),
    ("public_key", "TEXT"),
    # --- Lifecycle timestamps (ours) ---
    ("first_seen", "INTEGER"),
    ("last_seen", "INTEGER"),
    ("last_ping", "INTEGER"),
    ("last_hops", "INTEGER"),
    ("last_telemetry", "INTEGER"),
    ("last_position", "INTEGER"),
    ("online_since", "INTEGER"),
    ("uptime_seconds", "INTEGER"),
    # --- Signal of the last received packet ---
    ("last_rx_snr", "REAL"),
    ("last_rx_rssi", "REAL"),
    # --- Latest sensor values (extracted from raw telemetry) ---
    ("battery_level", "REAL"),
    ("battery_voltage", "REAL"),
    ("channel_utilization", "REAL"),
    ("air_util_tx", "REAL"),
    ("temperature", "REAL"),
    ("humidity", "REAL"),
    ("pressure", "REAL"),
    # --- Activity counters (ours) ---
    ("non_message_day", "TEXT"),
    ("telemetry_count_total", "INTEGER DEFAULT 0"),
    ("telemetry_count_daily", "INTEGER DEFAULT 0"),
    ("nodeinfo_count_total", "INTEGER DEFAULT 0"),
    ("nodeinfo_count_daily", "INTEGER DEFAULT 0"),
    ("position_count_total", "INTEGER DEFAULT 0"),
    ("position_count_daily", "INTEGER DEFAULT 0"),
    ("other_count_total", "INTEGER DEFAULT 0"),
    ("other_count_daily", "INTEGER DEFAULT 0"),
    # --- Raw meshtastic record, verbatim ---
    # A snapshot of the device's accumulated node-DB entry (interface.nodes),
    # which merges every packet type (user/name, position, deviceMetrics,
    # lastHeard, ...). This is the whole "what the device knows" picture, not a
    # single packet's slice -- see _lookup_interface_node().
    ("raw_node_json", "TEXT"),
]

# Fields that, once set, must not be overwritten by later upserts.
PRESERVE_IF_EXISTS = {"first_seen"}


# Channel configuration mirrored from the device (localNode.channels). The
# packet stream only ever carries a channel index, never the name/PSK -- those
# live on the radio -- so we snapshot them here on connect. Keyed by index.
CHANNEL_COLUMNS = [
    ("name", "TEXT"),
    ("role", "TEXT"),  # PRIMARY / SECONDARY (DISABLED slots are not stored)
    ("psk", "TEXT"),  # base64, exactly as the Meshtastic app shows it
    ("psk_hex", "TEXT"),
    ("psk_size", "INTEGER"),  # byte length: 0 none, 1 default key, 16 AES128, 32 AES256
    ("uplink_enabled", "INTEGER"),
    ("downlink_enabled", "INTEGER"),
    ("position_precision", "INTEGER"),
    ("raw_json", "TEXT"),
    ("updated_at", "INTEGER"),
]

CHANNEL_ROLE_NAMES = {0: "DISABLED", 1: "PRIMARY", 2: "SECONDARY"}


def init_db():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        # raw_json holds the verbatim packet (it contains `decoded`). reply_id /
        # thread_root drive whole-tree pagination (see _load_channel_threads);
        # emoji is the tapback reaction (NULL for a normal message), promoted to
        # a column so list reads never parse JSON.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rx_time INTEGER NOT NULL,
                channel_index INTEGER,
                channel_key TEXT,
                from_id TEXT,
                to_id TEXT,
                hops INTEGER,
                portnum TEXT,
                text TEXT,
                rx_rssi REAL,
                rx_snr REAL,
                packet_id INTEGER,
                reply_id INTEGER,
                thread_root INTEGER,
                emoji TEXT,
                raw_json TEXT
            )
            """
        )
        node_columns_sql = ",\n                ".join(
            [f"{name} {decl}" for name, decl in NODE_COLUMNS]
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                {node_columns_sql}
            )
            """
        )
        channel_columns_sql = ",\n                ".join(
            [f"{name} {decl}" for name, decl in CHANNEL_COLUMNS]
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS channels (
                channel_index INTEGER PRIMARY KEY,
                {channel_columns_sql}
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_key_time ON messages(channel_key, rx_time DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_packet_id ON messages(packet_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(channel_key, thread_root)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_last_seen ON nodes(last_seen DESC)")
        conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        conn.commit()


def _upsert_node(node_id, **updates):
    if not node_id:
        return
    columns = ["node_id"] + list(updates.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_parts = []
    for column in updates.keys():
        if column in PRESERVE_IF_EXISTS:
            update_parts.append(f"{column}=COALESCE(nodes.{column}, excluded.{column})")
        else:
            update_parts.append(f"{column}=excluded.{column}")
    update_clause = ", ".join(update_parts)
    values = [node_id] + list(updates.values())
    sql = (
        f"INSERT INTO nodes ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(node_id) DO UPDATE SET {update_clause}"
    )
    with _db_connect() as conn:
        conn.execute(sql, values)
        conn.commit()


def _channel_to_record(channel):
    """Flatten a Meshtastic Channel protobuf into our column dict."""
    settings = getattr(channel, "settings", None)
    index = _coerce_int(getattr(channel, "index", 0)) or 0
    role = getattr(channel, "role", 0)
    name = getattr(settings, "name", "") if settings is not None else ""
    psk_raw = getattr(settings, "psk", b"") if settings is not None else b""
    psk_bytes = bytes(psk_raw) if psk_raw else b""
    module_settings = getattr(settings, "module_settings", None) if settings is not None else None
    position_precision = (
        _coerce_int(getattr(module_settings, "position_precision", None))
        if module_settings is not None
        else None
    )
    try:
        from google.protobuf.json_format import MessageToDict

        raw = MessageToDict(channel, preserving_proto_field_name=True)
    except Exception:  # pragma: no cover - best effort snapshot
        raw = None
    return {
        "channel_index": index,
        "name": name or None,
        "role": CHANNEL_ROLE_NAMES.get(role, str(role)),
        "psk": base64.b64encode(psk_bytes).decode("ascii") if psk_bytes else "",
        "psk_hex": psk_bytes.hex(),
        "psk_size": len(psk_bytes),
        "uplink_enabled": 1 if (settings is not None and getattr(settings, "uplink_enabled", False)) else 0,
        "downlink_enabled": 1 if (settings is not None and getattr(settings, "downlink_enabled", False)) else 0,
        "position_precision": position_precision,
        "raw_json": _json_dumps(raw) if raw is not None else None,
        "updated_at": int(time.time()),
    }


def sync_channels(interface):
    """Snapshot the device's configured channels into the channels table.

    Disabled slots are skipped, and the table is replaced wholesale so it
    always matches the device's current truth (a renamed/removed channel
    doesn't leave a stale row behind).
    """
    ln = getattr(interface, "localNode", None)
    channels = getattr(ln, "channels", None) if ln is not None else None
    if not channels:
        return
    records = [
        _channel_to_record(channel)
        for channel in channels
        if getattr(channel, "role", 0) != 0  # skip DISABLED slots
    ]
    if not records:
        return
    columns = ["channel_index"] + [name for name, _ in CHANNEL_COLUMNS]
    placeholders = ", ".join(["?"] * len(columns))
    with _db_connect() as conn:
        conn.execute("DELETE FROM channels")
        conn.executemany(
            f"INSERT INTO channels ({', '.join(columns)}) VALUES ({placeholders})",
            [[record.get(col) for col in columns] for record in records],
        )
        conn.commit()
    logging.info("Synced %d channel(s) from device", len(records))


def _channel_key_to_index(channel_key):
    """Map a message channel_key back to a device channel index.

    The primary channel rides packets with no index, landing in our "unknown"
    bucket -> index 0. Secondary channels carry their numeric index directly.
    """
    if channel_key in (None, "", "unknown"):
        return 0
    try:
        return int(channel_key)
    except (TypeError, ValueError):
        return None


def _channel_key_label(psk_size):
    if psk_size is None:
        return None
    if psk_size == 0:
        return "None (unencrypted)"
    if psk_size == 1:
        return "Default key"
    if psk_size == 16:
        return "AES-128"
    if psk_size == 32:
        return "AES-256"
    return f"{psk_size * 8}-bit"


def _channel_display_name(channel_index, name):
    if channel_index == 0:
        return "Primary"
    if name:
        return name
    if channel_index is not None:
        return f"Channel {channel_index}"
    return "Channel ?"


def _resolve_thread_root(conn, channel_key, packet_id, reply_id):
    """Pick the thread_root for a message.

    A message that replies to a stored parent inherits the parent's root, so
    the whole conversation shares one root. A root message (no reply) or a
    reply whose parent hasn't been received yet roots its own thread via its
    packet id. Messages without a packet id can't be threaded; they stay NULL
    and are treated as singletons at read time (COALESCE(thread_root, -id)).
    """
    if reply_id is not None:
        row = conn.execute(
            "SELECT thread_root FROM messages "
            "WHERE channel_key = ? AND packet_id = ? AND thread_root IS NOT NULL "
            "ORDER BY rx_time LIMIT 1",
            (channel_key, reply_id),
        ).fetchone()
        if row and row["thread_root"] is not None:
            return row["thread_root"]
    return packet_id


def _reconcile_thread_root(conn, channel_key, packet_id, thread_root):
    """Adopt replies that arrived before this (their parent) message.

    When a parent is received after its replies, those replies will have
    rooted themselves (thread_root == own packet_id). Re-stamp each such
    stranded reply and its descendants -- which all share that stranded root
    -- onto this message's thread, merging the two trees into one.
    """
    if packet_id is None or thread_root is None:
        return
    stranded = conn.execute(
        "SELECT DISTINCT packet_id FROM messages "
        "WHERE channel_key = ? AND reply_id = ? AND packet_id IS NOT NULL "
        "AND thread_root = packet_id",
        (channel_key, packet_id),
    ).fetchall()
    for row in stranded:
        old_root = row["packet_id"]
        if old_root == thread_root:
            continue
        conn.execute(
            "UPDATE messages SET thread_root = ? WHERE channel_key = ? AND thread_root = ?",
            (thread_root, channel_key, old_root),
        )


def _insert_message(**message):
    """Insert a message, stamping its thread_root and adopting any earlier
    replies that were waiting for it."""
    channel_key = message.get("channel_key")
    packet_id = message.get("packet_id")
    reply_id = message.get("reply_id")
    with _db_connect() as conn:
        message["thread_root"] = _resolve_thread_root(conn, channel_key, packet_id, reply_id)
        columns = list(message.keys())
        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({placeholders})"
        conn.execute(sql, list(message.values()))
        _reconcile_thread_root(conn, channel_key, packet_id, message["thread_root"])
        conn.commit()


def _get_non_message_counts(node_id):
    with _db_connect() as conn:
        row = conn.execute(
            """
            SELECT
                non_message_day,
                telemetry_count_total,
                telemetry_count_daily,
                nodeinfo_count_total,
                nodeinfo_count_daily,
                position_count_total,
                position_count_daily,
                other_count_total,
                other_count_daily
            FROM nodes
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
    return dict(row) if row else None


def _build_non_message_updates(current_day, counts, increment_type):
    day_changed = counts is None or counts.get("non_message_day") != current_day
    if not day_changed and increment_type is None:
        return {}

    totals = {
        "telemetry": int(counts.get("telemetry_count_total") or 0) if counts else 0,
        "nodeinfo": int(counts.get("nodeinfo_count_total") or 0) if counts else 0,
        "position": int(counts.get("position_count_total") or 0) if counts else 0,
        "other": int(counts.get("other_count_total") or 0) if counts else 0,
    }
    daily = {
        "telemetry": int(counts.get("telemetry_count_daily") or 0) if counts and not day_changed else 0,
        "nodeinfo": int(counts.get("nodeinfo_count_daily") or 0) if counts and not day_changed else 0,
        "position": int(counts.get("position_count_daily") or 0) if counts and not day_changed else 0,
        "other": int(counts.get("other_count_daily") or 0) if counts and not day_changed else 0,
    }

    if increment_type in totals:
        totals[increment_type] += 1
        daily[increment_type] += 1

    return {
        "non_message_day": current_day,
        "telemetry_count_total": totals["telemetry"],
        "telemetry_count_daily": daily["telemetry"],
        "nodeinfo_count_total": totals["nodeinfo"],
        "nodeinfo_count_daily": daily["nodeinfo"],
        "position_count_total": totals["position"],
        "position_count_daily": daily["position"],
        "other_count_total": totals["other"],
        "other_count_daily": daily["other"],
    }


def _extract_portnum(decoded):
    if not isinstance(decoded, dict):
        return None
    return decoded.get("portnum") or decoded.get("portNum") or decoded.get("portnum_name")


def _extract_channel_info(packet):
    if not isinstance(packet, dict):
        return None, "unknown"
    channel_index = _coerce_int(packet.get("channelIndex") or packet.get("chan"))
    channel_key_raw = packet.get("channel")
    if channel_key_raw is None:
        channel_key_raw = (
            packet.get("channelHash")
            or packet.get("channelId")
            or packet.get("channel_id")
            or packet.get("channel_key")
            or packet.get("channelKey")
        )
    channel_key = _coerce_str(channel_key_raw)
    if channel_key is None and channel_index is not None:
        channel_key = str(channel_index)
    if channel_index is None and isinstance(channel_key, str) and channel_key.isdigit():
        channel_index = _coerce_int(channel_key)
    if channel_key is None:
        channel_key = "unknown"
    return channel_index, channel_key


def _extract_text(decoded, portnum):
    if not isinstance(decoded, dict):
        return None
    if decoded.get("text"):
        return str(decoded.get("text"))
    if not portnum:
        return None
    if "TEXT" not in str(portnum).upper():
        return None
    payload = decoded.get("payload")
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        return payload
    return None


def _extract_hops(packet):
    if not isinstance(packet, dict):
        return None
    hops = packet.get("hopsAway")
    if hops is None:
        hops = packet.get("hops")
    hops = _coerce_int(hops)
    if hops is not None:
        return hops
    hop_start = _coerce_int(packet.get("hopStart") or packet.get("hop_start"))
    hop_limit = _coerce_int(packet.get("hopLimit") or packet.get("hop_limit"))
    if hop_start is None or hop_limit is None:
        return None
    return max(hop_start - hop_limit, 0)


def _extract_nodeinfo(decoded):
    if not isinstance(decoded, dict):
        return {}
    user = decoded.get("user") or decoded.get("nodeInfo") or decoded.get("nodeinfo")
    if not isinstance(user, dict):
        return {}
    return {
        "short_name": user.get("shortName") or user.get("short_name"),
        "long_name": user.get("longName") or user.get("long_name"),
        "hw_model": user.get("hwModel") or user.get("hw_model"),
        "role": _coerce_str(user.get("role")),
        "macaddr": _coerce_b64(user.get("macaddr") or user.get("macAddr")),
        "public_key": _coerce_b64(user.get("publicKey") or user.get("public_key")),
    }


def _lookup_interface_node(interface, node_id):
    """Return the device's accumulated node-DB entry for ``node_id``.

    ``interface.nodes`` is the device's merged per-node record -- it carries
    everything learned from every packet type (the NODEINFO user/name, latest
    position, deviceMetrics, lastHeard, hopsAway, ...), which a single packet
    never has on its own. Returns the dict, or None if not found.
    """
    if interface is None or not node_id:
        return None
    nodes = getattr(interface, "nodes", None)
    if not isinstance(nodes, dict):
        return None

    keys_to_try = [node_id]
    if isinstance(node_id, str):
        stripped = node_id.lstrip("!")
        if stripped != node_id:
            keys_to_try.append(stripped)
        try:
            keys_to_try.append(int(stripped, 16))
        except ValueError:
            pass
    if isinstance(node_id, int):
        keys_to_try.append(f"!{node_id:08x}")

    for key in keys_to_try:
        if key in nodes:
            node_meta = nodes.get(key)
            if isinstance(node_meta, dict):
                return node_meta

    if isinstance(node_id, str):
        for key, value in nodes.items():
            if isinstance(key, str) and key.lower() == node_id.lower():
                if isinstance(value, dict):
                    return value
    return None


def _extract_nodeinfo_from_interface(interface, node_id):
    node_meta = _lookup_interface_node(interface, node_id)
    if not isinstance(node_meta, dict):
        return {}

    user = node_meta.get("user") if isinstance(node_meta.get("user"), dict) else {}
    return {
        "short_name": user.get("shortName")
        or user.get("short_name")
        or node_meta.get("shortName")
        or node_meta.get("short_name"),
        "long_name": user.get("longName")
        or user.get("long_name")
        or node_meta.get("longName")
        or node_meta.get("long_name"),
        "hw_model": user.get("hwModel")
        or user.get("hw_model")
        or node_meta.get("hwModel")
        or node_meta.get("hw_model"),
        "role": _coerce_str(user.get("role") or node_meta.get("role")),
        "macaddr": _coerce_b64(
            user.get("macaddr") or user.get("macAddr") or node_meta.get("macaddr")
        ),
        "public_key": _coerce_b64(
            user.get("publicKey")
            or user.get("public_key")
            or node_meta.get("publicKey")
            or node_meta.get("public_key")
        ),
    }


def _extract_node_id(packet, decoded):
    if isinstance(packet, dict):
        for key in ("fromId", "from", "sender", "senderId", "from_id", "sender_id"):
            value = packet.get(key)
            if value is not None:
                return str(value)
    if isinstance(decoded, dict):
        for key in ("fromId", "from", "sender", "senderId", "from_id", "sender_id", "node_id", "nodeId"):
            value = decoded.get(key)
            if value is not None:
                return str(value)
        user = decoded.get("user") or decoded.get("nodeInfo") or decoded.get("nodeinfo")
        if isinstance(user, dict):
            for key in ("id", "userId", "nodeId", "node_id", "num"):
                value = user.get(key)
                if value is not None:
                    return str(value)
    return None


def _extract_telemetry(decoded):
    if not isinstance(decoded, dict):
        return None
    telemetry = decoded.get("telemetry")
    if telemetry is None and isinstance(decoded.get("payload"), dict):
        telemetry = decoded.get("payload")
    if isinstance(telemetry, dict):
        return telemetry
    return None


def _extract_position(decoded):
    if not isinstance(decoded, dict):
        return None
    position = decoded.get("position") or decoded.get("pos")
    if isinstance(position, dict):
        return position
    return None


def _classify_non_message(message_text, portnum, telemetry, position, nodeinfo):
    if message_text:
        return None
    if telemetry:
        return "telemetry"
    if position:
        return "position"
    if nodeinfo:
        return "nodeinfo"
    port_label = str(portnum).upper() if portnum else ""
    if "TELEMETRY" in port_label:
        return "telemetry"
    if "POSITION" in port_label:
        return "position"
    if "NODEINFO" in port_label:
        return "nodeinfo"
    return "other"


def _extract_sensor_values(telemetry):
    if not isinstance(telemetry, dict):
        return {}
    device = telemetry.get("deviceMetrics") or telemetry.get("device_metrics") or {}
    env = telemetry.get("environmentMetrics") or telemetry.get("environment_metrics") or {}
    return {
        "battery_level": device.get("batteryLevel") or device.get("battery_level"),
        "battery_voltage": device.get("voltage") or device.get("batteryVoltage") or device.get("battery_voltage"),
        "channel_utilization": device.get("channelUtilization") or device.get("channel_utilization"),
        "air_util_tx": device.get("airUtilTx") or device.get("air_util_tx"),
        "uptime_seconds": device.get("uptimeSeconds") or device.get("uptime_seconds"),
        "temperature": env.get("temperature"),
        "humidity": env.get("relativeHumidity") or env.get("humidity"),
        "pressure": env.get("barometricPressure") or env.get("pressure"),
    }


def handle_packet(packet, interface=None):
    if not isinstance(packet, dict):
        return
    decoded = packet.get("decoded") or {}
    portnum = _extract_portnum(decoded)

    rx_time = _normalize_timestamp(packet.get("rxTime") or packet.get("rx_time") or time.time())
    channel_index, channel_key = _extract_channel_info(packet)
    hops = _extract_hops(packet)

    from_id = _extract_node_id(packet, decoded)
    to_id = packet.get("toId") or packet.get("to")
    if to_id is not None:
        to_id = str(to_id)

    message_text = _extract_text(decoded, portnum)
    if isinstance(message_text, str):
        message_text = message_text.strip()
        if not message_text:
            message_text = None

    nodeinfo = _extract_nodeinfo(decoded)
    telemetry = _extract_telemetry(decoded)
    position = _extract_position(decoded)
    non_message_type = _classify_non_message(message_text, portnum, telemetry, position, nodeinfo)

    if message_text:
        reply_id = None
        emoji = None
        if isinstance(decoded, dict):
            reply_id = _coerce_int(decoded.get("replyId") or decoded.get("reply_id"))
            emoji = _coerce_str(decoded.get("emoji")) if decoded.get("emoji") else None
        _insert_message(
            rx_time=rx_time,
            channel_index=channel_index,
            channel_key=channel_key,
            from_id=from_id,
            to_id=to_id,
            hops=hops,
            portnum=str(portnum) if portnum else None,
            text=message_text,
            rx_rssi=packet.get("rxRssi"),
            rx_snr=packet.get("rxSnr"),
            packet_id=_coerce_int(packet.get("id")),
            reply_id=reply_id,
            emoji=emoji,
            raw_json=_json_dumps(packet),
        )

    updates = {"last_seen": rx_time, "first_seen": rx_time}
    updates.update({k: v for k, v in nodeinfo.items() if v is not None})
    updates.update(
        {k: v for k, v in _extract_nodeinfo_from_interface(interface, from_id).items() if v is not None}
    )
    # Snapshot the device's full accumulated record for this node (name +
    # position + metrics + ...), which a single packet never carries alone.
    node_meta = _lookup_interface_node(interface, from_id)
    if isinstance(node_meta, dict) and node_meta:
        updates["raw_node_json"] = _json_dumps(node_meta)
    if hops is not None:
        updates["last_hops"] = hops

    rx_snr = packet.get("rxSnr")
    if rx_snr is not None:
        updates["last_rx_snr"] = rx_snr
    rx_rssi = packet.get("rxRssi")
    if rx_rssi is not None:
        updates["last_rx_rssi"] = rx_rssi

    if portnum and any(keyword in str(portnum).upper() for keyword in PING_KEYWORDS):
        updates["last_ping"] = rx_time

    if telemetry:
        updates["last_telemetry"] = rx_time
        sensor_values = {k: v for k, v in _extract_sensor_values(telemetry).items() if v is not None}
        updates.update(sensor_values)
        uptime_seconds = sensor_values.get("uptime_seconds")
        if uptime_seconds is not None:
            try:
                updates["online_since"] = int(rx_time) - int(uptime_seconds)
            except (TypeError, ValueError):
                pass

    if position:
        updates["last_position"] = rx_time

    if from_id:
        counts = _get_non_message_counts(from_id)
        current_day = _local_day(rx_time)
        updates.update(_build_non_message_updates(current_day, counts, non_message_type))

    _upsert_node(from_id, **updates)


def on_receive(packet, interface):
    try:
        handle_packet(packet, interface)
    except Exception:
        logging.exception("Failed to handle packet")


def on_connection(interface, topic=pub.AUTO_TOPIC):
    logging.info("Meshtastic connected on %s", DEVICE_PATH)
    # By connection.established the device has downloaded its config, so
    # localNode.channels is populated -- snapshot it for the channel modal.
    try:
        sync_channels(interface)
    except Exception:
        logging.exception("Failed to sync channels")


def on_connection_lost(interface, topic=pub.AUTO_TOPIC):
    logging.warning("Meshtastic connection lost")


def start_listener():
    def worker():
        subscribed = False
        while True:
            try:
                if not subscribed:
                    pub.subscribe(on_receive, "meshtastic.receive")
                    pub.subscribe(on_connection, "meshtastic.connection.established")
                    pub.subscribe(on_connection_lost, "meshtastic.connection.lost")
                    subscribed = True
                interface = SerialInterface(devPath=DEVICE_PATH)
                while True:
                    time.sleep(1)
            except Exception:
                logging.exception("Meshtastic connection failed; retrying")
                time.sleep(LISTEN_RETRY_SECONDS)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


@app.route("/")
def index():
    return redirect(url_for("messages_default"))


def _get_channels_config():
    with _db_connect() as conn:
        rows = conn.execute("SELECT * FROM channels").fetchall()
    return {row["channel_index"]: dict(row) for row in rows}


def _get_available_channels():
    config = _get_channels_config()
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT channel_key, channel_index, MAX(rx_time) AS last_rx
            FROM messages
            WHERE channel_key IS NOT NULL
            GROUP BY channel_key, channel_index
            ORDER BY last_rx DESC
            """
        ).fetchall()
    channels = []
    for row in rows:
        channel = dict(row)
        index = _channel_key_to_index(channel.get("channel_key"))
        if index is None:
            index = channel.get("channel_index")
        cfg = config.get(index) if index is not None else None
        channel["name"] = cfg.get("name") if cfg else None
        channel["display_name"] = _channel_display_name(index, channel["name"])
        channels.append(channel)
    return channels


def _pick_default_channel_key(channels):
    for channel in channels:
        if channel.get("channel_index") == DEFAULT_CHANNEL_INDEX:
            return channel.get("channel_key")
    if channels:
        return channels[0].get("channel_key")
    return str(DEFAULT_CHANNEL_INDEX)


def _find_channel_info(channels, channel_key):
    for channel in channels:
        if channel.get("channel_key") == channel_key:
            return channel
    return {"channel_key": channel_key, "channel_index": None}


# Threads per page for the message list's scroll-back pagination.
TREE_PAGE_SIZE = 40

# Shared column list for message reads. ``thread_root`` is exposed (with a
# per-row fallback for un-threadable rows) so the client can tell which tree a
# message belongs to when merging live updates.
_MESSAGE_SELECT = """
    m.id, m.rx_time, m.channel_index, m.channel_key,
    m.from_id, m.to_id, m.hops, m.portnum, m.text,
    m.rx_rssi, m.rx_snr, m.packet_id, m.reply_id, m.emoji,
    COALESCE(m.thread_root, -m.id) AS thread_root,
    n.short_name, n.long_name
"""


def _row_to_message(row):
    """Enrich a message row: tapback flag, avatar colors. All fields come from
    columns now, so no JSON parsing happens on the list path."""
    message = dict(row)
    message["reply_id"] = _coerce_int(message.get("reply_id"))
    message["is_tapback"] = bool(message.get("emoji"))
    message.update(_node_avatar_colors(message.get("from_id")))
    return message


def _parse_cursor(value):
    """Parse a ``<recency>:<root>`` pagination cursor into a tuple, or None."""
    if not value:
        return None
    try:
        recency, root = value.split(":")
        return (int(recency), int(root))
    except (ValueError, AttributeError):
        return None


def _select_thread_page(conn, channel_key, before, limit):
    """Pick the newest ``limit`` thread roots, ordered by recency (newest
    first). ``before`` is a ``(recency, root)`` keyset cursor for older pages."""
    params = [channel_key]
    having = ""
    if before is not None:
        recency, root = before
        having = "HAVING recency < ? OR (recency = ? AND root < ?)"
        params.extend([recency, recency, root])
    params.append(limit)
    return conn.execute(
        f"""
        SELECT root, MAX(rx_time) AS recency FROM (
            SELECT COALESCE(thread_root, -id) AS root, rx_time
            FROM messages WHERE channel_key = ?
        ) GROUP BY root
        {having}
        ORDER BY recency DESC, root DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def _load_channel_threads(channel_key, before=None, limit=TREE_PAGE_SIZE):
    """Load one page of whole conversation trees, newest trees last.

    Pagination is by tree (not message): each page is a run of complete trees
    ordered by recency, so a tree's replies and tapbacks always travel
    together. ``before`` is a ``<recency>:<root>`` cursor string (or None for
    the newest page). Returns ``(messages, next_cursor)``; ``next_cursor`` is a
    cursor string, or None once the start of history is reached.
    """
    with _db_connect() as conn:
        roots = _select_thread_page(conn, channel_key, _parse_cursor(before), limit)
        if not roots:
            return [], None
        root_values = [r["root"] for r in roots]
        placeholders = ",".join("?" * len(root_values))
        rows = conn.execute(
            f"""
            SELECT {_MESSAGE_SELECT}
            FROM messages m
            LEFT JOIN nodes n ON n.node_id = m.from_id
            WHERE m.channel_key = ? AND COALESCE(m.thread_root, -m.id) IN ({placeholders})
            ORDER BY m.rx_time ASC, m.id ASC
            """,
            [channel_key, *root_values],
        ).fetchall()
    messages_list = [_row_to_message(row) for row in rows]
    # The oldest tree on this page is the cursor for the next, older page. A
    # short page means there are no older trees left.
    next_cursor = None
    if len(roots) == limit:
        last = roots[-1]
        next_cursor = f"{last['recency']}:{last['root']}"
    return messages_list, next_cursor


def _load_thread_updates(channel_key, since):
    """Return every message of any tree with activity at/after ``since``.

    Whole touched trees are returned (not just the new rows) so the client can
    drop in a complete tree -- including ones bumped forward from a page it had
    never loaded -- and simply re-sort by recency.
    """
    with _db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_MESSAGE_SELECT}
            FROM messages m
            LEFT JOIN nodes n ON n.node_id = m.from_id
            WHERE m.channel_key = ? AND COALESCE(m.thread_root, -m.id) IN (
                SELECT COALESCE(thread_root, -id) FROM messages
                WHERE channel_key = ? AND rx_time >= ?
            )
            ORDER BY m.rx_time ASC, m.id ASC
            """,
            (channel_key, channel_key, since),
        ).fetchall()
    return [_row_to_message(row) for row in rows]


def _render_messages(channel_key, channels):
    messages_list, next_cursor = _load_channel_threads(channel_key)
    current_channel = _find_channel_info(channels, channel_key)
    return render_template(
        "messages.html",
        messages=messages_list,
        next_cursor=next_cursor,
        channels=channels,
        current_channel=current_channel,
        auto_refresh_seconds=AUTO_REFRESH_SECONDS,
    )


@app.route("/api/messages/<channel_key>")
def messages_api(channel_key):
    messages_list, next_cursor = _load_channel_threads(
        channel_key, before=request.args.get("before")
    )
    return jsonify({"messages": messages_list, "next_cursor": next_cursor})


@app.route("/api/messages/<channel_key>/updates")
def messages_updates_api(channel_key):
    since = _coerce_int(request.args.get("since")) or 0
    return jsonify({"messages": _load_thread_updates(channel_key, since)})


@app.route("/messages")
def messages_default():
    channels = _get_available_channels()
    channel_key = _pick_default_channel_key(channels)
    if channels:
        return redirect(url_for("messages_channel", channel_key=channel_key))
    return _render_messages(channel_key, channels)


@app.route("/messages/<channel_key>")
def messages_channel(channel_key):
    channels = _get_available_channels()
    return _render_messages(channel_key, channels)


# Field membership for the on-demand combined JSON shown when a detail view is
# expanded. "raw" is the verbatim mesh data; "derived" is everything we
# computed and stored in columns (identity, threading, sensors, bookkeeping).
# Keys not listed (joined node names, avatar colors) are presentation-only and
# stay out of the blob.
_MSG_DERIVED_KEYS = (
    "id", "packet_id", "channel_index", "channel_key",
    "from_id", "to_id", "portnum", "text", "hops", "emoji", "is_tapback",
    "reply_id", "thread_root", "rx_time",
)


def _pick(record, keys):
    return {k: record.get(k) for k in keys}


@app.route("/api/message/<int:message_id>")
def message_detail_api(message_id):
    with _db_connect() as conn:
        row = conn.execute(
            """
            SELECT m.*, n.short_name, n.long_name
            FROM messages m
            LEFT JOIN nodes n ON n.node_id = m.from_id
            WHERE m.id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "not found", "message_id": message_id}), 404
        message = dict(row)

        raw = _safe_json_loads(message.pop("raw_json", None))
        decoded = raw.get("decoded") if isinstance(raw, dict) else None
        message["decoded"] = decoded
        message["raw"] = raw

        # Columns are authoritative; fall back to the raw payload for legacy
        # rows that predate the reply_id/emoji columns.
        reply_id = message.get("reply_id")
        emoji = message.get("emoji")
        if isinstance(decoded, dict):
            if reply_id is None:
                reply_id = decoded.get("replyId") or decoded.get("reply_id")
            if emoji is None:
                emoji = decoded.get("emoji")
        message["reply_id"] = _coerce_int(reply_id)
        message["emoji"] = emoji
        message["is_tapback"] = bool(emoji)
        reply_id = message["reply_id"]

        reply_to = None
        if reply_id is not None:
            # Older rows predate the packet_id column and only carry the mesh
            # packet id inside raw_json, so match against either source.
            parent = conn.execute(
                """
                SELECT m.id, m.packet_id, m.text, m.from_id, m.rx_time,
                       n.short_name, n.long_name
                FROM messages m
                LEFT JOIN nodes n ON n.node_id = m.from_id
                WHERE m.id != ?
                  AND (m.packet_id = ?
                       OR json_extract(m.raw_json, '$.id') = ?)
                ORDER BY m.rx_time DESC
                LIMIT 1
                """,
                (message_id, reply_id, reply_id),
            ).fetchone()
            if parent is not None:
                reply_to = dict(parent)
                reply_to.update(_node_avatar_colors(reply_to.get("from_id")))
        message["reply_to"] = reply_to

        # Recipient node info, so the UI can show an avatar + name for direct
        # messages (broadcasts have no specific recipient node).
        to_node = None
        to_id = message.get("to_id")
        if to_id is not None and str(to_id) not in ("^all", "4294967295", "!ffffffff"):
            recipient = conn.execute(
                """
                SELECT node_id, short_name, long_name
                FROM nodes
                WHERE node_id = ?
                """,
                (to_id,),
            ).fetchone()
            if recipient is not None:
                to_node = dict(recipient)
                to_node.update(_node_avatar_colors(to_node.get("node_id")))
        message["to_node"] = to_node

        # Relay node: only the last byte of the relaying node's id travels on
        # the wire (1 byte = 256 possibilities), so it can't be resolved
        # uniquely. Surface every known node whose id ends in that byte; the UI
        # shows them as "x or y or z". Most-recently-seen nodes come first as
        # the more likely relay.
        relay_byte = _coerce_int(raw.get("relayNode") if isinstance(raw, dict) else None)
        relay_candidates = []
        if relay_byte is not None and 0 <= relay_byte <= 0xFF:
            suffix = f"{relay_byte:02x}"
            matches = conn.execute(
                """
                SELECT node_id, short_name, long_name
                FROM nodes
                WHERE lower(substr(node_id, -2)) = ?
                ORDER BY last_seen DESC
                """,
                (suffix,),
            ).fetchall()
            for match in matches:
                cand = dict(match)
                cand.update(_node_avatar_colors(cand.get("node_id")))
                relay_candidates.append(cand)
            # A 0-hop packet reached us directly from its origin with no relay
            # in between, so the relay must be the sender. When the sender is
            # among the byte matches, drop the other (impossible) candidates.
            if _coerce_int(message.get("hops")) == 0:
                from_id = message.get("from_id")
                sender_only = [c for c in relay_candidates if c.get("node_id") == from_id]
                if sender_only:
                    relay_candidates = sender_only
        message["relay_node"] = relay_byte
        message["relay_candidates"] = relay_candidates

    message.update(_node_avatar_colors(message.get("from_id")))
    # Combined sections, assembled on demand for the expand-for-JSON view.
    message["sections"] = {
        "raw": raw,
        "derived": _pick(message, _MSG_DERIVED_KEYS),
    }
    return jsonify({"message": message})


@app.route("/nodes")
def nodes():
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM nodes
            ORDER BY last_seen DESC
            """
        ).fetchall()
    node_list = [dict(row) for row in rows]
    for node in node_list:
        node.update(_node_avatar_colors(node.get("node_id")))
    return render_template(
        "nodes.html",
        nodes=node_list,
        auto_refresh_seconds=AUTO_REFRESH_SECONDS,
    )


@app.route("/api/nodes")
def nodes_api():
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                node_id,
                short_name,
                long_name,
                hw_model,
                last_seen,
                last_ping,
                last_hops,
                battery_level,
                battery_voltage,
                temperature,
                humidity,
                pressure,
                telemetry_count_total,
                telemetry_count_daily,
                nodeinfo_count_total,
                nodeinfo_count_daily,
                position_count_total,
                position_count_daily,
                other_count_total,
                other_count_daily
            FROM nodes
            ORDER BY last_seen DESC
            """
        ).fetchall()
    node_list = [dict(row) for row in rows]
    for node in node_list:
        node.update(_node_avatar_colors(node.get("node_id")))
    return jsonify({"nodes": node_list})


# Field membership for a node's on-demand combined JSON (see _MSG_DERIVED_KEYS).
# Everything we computed/stored in columns, presented as one "derived" section.
_NODE_DERIVED_KEYS = (
    "node_id",
    "short_name", "long_name", "hw_model", "role", "macaddr", "public_key",
    "first_seen", "last_seen", "last_ping", "last_telemetry", "last_position",
    "last_hops", "last_rx_snr", "last_rx_rssi", "online_since", "uptime_seconds",
    "battery_level", "battery_voltage", "channel_utilization", "air_util_tx",
    "temperature", "humidity", "pressure",
    "non_message_day",
    "telemetry_count_total", "telemetry_count_daily",
    "nodeinfo_count_total", "nodeinfo_count_daily",
    "position_count_total", "position_count_daily",
    "other_count_total", "other_count_daily",
)


@app.route("/api/nodes/<path:node_id>")
def node_detail_api(node_id):
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
    if row is None:
        return jsonify({"error": "not found", "node_id": node_id}), 404
    node = dict(row)
    node.update(_node_avatar_colors(node.get("node_id")))

    # Raw = the device's accumulated node record (one merged object).
    raw = _safe_json_loads(node.get("raw_node_json")) or {}
    # Back-compat: the detail view's Position section reads node.position.
    if isinstance(raw, dict):
        node["position"] = raw.get("position")

    node["sections"] = {
        "raw": raw,
        "derived": _pick(node, _NODE_DERIVED_KEYS),
    }
    return jsonify({"node": node})


# Field membership for a channel's on-demand combined JSON (see _MSG_DERIVED_KEYS).
_CHANNEL_DERIVED_KEYS = (
    "channel_index", "name", "role", "psk", "psk_hex", "psk_size",
    "uplink_enabled", "downlink_enabled", "position_precision", "updated_at",
)


@app.route("/api/channels/<channel_key>")
def channel_detail_api(channel_key):
    channel_index = _channel_key_to_index(channel_key)
    row = None
    if channel_index is not None:
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE channel_index = ?",
                (channel_index,),
            ).fetchone()

    channel = dict(row) if row is not None else {}
    channel["channel_index"] = channel_index
    channel["channel_key"] = channel_key
    channel["configured"] = row is not None
    channel["display_name"] = _channel_display_name(channel_index, channel.get("name"))
    channel["key_label"] = _channel_key_label(channel.get("psk_size"))
    channel["uplink_enabled"] = bool(channel.get("uplink_enabled"))
    channel["downlink_enabled"] = bool(channel.get("downlink_enabled"))

    raw = _safe_json_loads(channel.get("raw_json")) if row is not None else None
    channel["sections"] = {
        "raw": raw,
        "derived": _pick(channel, _CHANNEL_DERIVED_KEYS),
    }
    return jsonify({"channel": channel})


app.jinja_env.filters["datetime"] = _format_time
app.jinja_env.filters["message_time"] = _format_message_time
app.jinja_env.filters["value"] = _format_value
app.jinja_env.filters["value_unit"] = _format_value_unit
app.jinja_env.filters["is_live"] = _is_live
app.jinja_env.filters["battery_summary"] = _battery_summary
app.jinja_env.filters["environment_summary"] = _environment_summary
app.jinja_env.filters["battery_info"] = _battery_info
app.jinja_env.filters["node_subtitle"] = _node_subtitle


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    start_listener()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
