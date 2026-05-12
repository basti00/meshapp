import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, url_for
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

DB_SCHEMA_VERSION = 3
DB_PATH = Path(__file__).with_name("meshapp.db")
DEVICE_PATH = os.environ.get("MESH_DEVICE", "/dev/ttyACM0")
DEFAULT_CHANNEL_INDEX = int(os.environ.get("MESH_CHANNEL", "0"))
AUTO_REFRESH_SECONDS = int(os.environ.get("MESH_AUTO_REFRESH", "10"))
LISTEN_RETRY_SECONDS = 5

PING_KEYWORDS = ("PING",)

app = Flask(__name__)


def _json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def _json_dumps(value):
    return json.dumps(value, default=_json_default, ensure_ascii=True)


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


def _format_value(value, digits=2):
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
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


def _db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        current_version = conn.execute("PRAGMA user_version;").fetchone()[0]
        if current_version != DB_SCHEMA_VERSION:
            conn.execute("DROP TABLE IF EXISTS messages")
            conn.execute("DROP TABLE IF EXISTS nodes")
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
                decoded_json TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                short_name TEXT,
                long_name TEXT,
                hw_model TEXT,
                last_seen INTEGER,
                last_ping INTEGER,
                last_hops INTEGER,
                last_telemetry INTEGER,
                last_position INTEGER,
                battery_level REAL,
                battery_voltage REAL,
                temperature REAL,
                humidity REAL,
                pressure REAL,
                telemetry_json TEXT,
                position_json TEXT,
                non_message_day TEXT,
                telemetry_count_total INTEGER DEFAULT 0,
                telemetry_count_daily INTEGER DEFAULT 0,
                nodeinfo_count_total INTEGER DEFAULT 0,
                nodeinfo_count_daily INTEGER DEFAULT 0,
                position_count_total INTEGER DEFAULT 0,
                position_count_daily INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_key_time ON messages(channel_key, rx_time DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_last_seen ON nodes(last_seen DESC)")
        conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        conn.commit()


def _upsert_node(node_id, **updates):
    if not node_id:
        return
    columns = ["node_id"] + list(updates.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_clause = ", ".join([f"{column}=excluded.{column}" for column in updates.keys()])
    values = [node_id] + list(updates.values())
    sql = (
        f"INSERT INTO nodes ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(node_id) DO UPDATE SET {update_clause}"
    )
    with _db_connect() as conn:
        conn.execute(sql, values)
        conn.commit()


def _insert_message(**message):
    columns = list(message.keys())
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO messages ({', '.join(columns)}) VALUES ({placeholders})"
    with _db_connect() as conn:
        conn.execute(sql, list(message.values()))
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
                position_count_daily
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
    }
    daily = {
        "telemetry": int(counts.get("telemetry_count_daily") or 0) if counts and not day_changed else 0,
        "nodeinfo": int(counts.get("nodeinfo_count_daily") or 0) if counts and not day_changed else 0,
        "position": int(counts.get("position_count_daily") or 0) if counts and not day_changed else 0,
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
    }


def _extract_nodeinfo_from_interface(interface, node_id):
    if interface is None or not node_id:
        return {}
    nodes = getattr(interface, "nodes", None)
    if not isinstance(nodes, dict):
        return {}

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

    node_meta = None
    for key in keys_to_try:
        if key in nodes:
            node_meta = nodes.get(key)
            break

    if node_meta is None and isinstance(node_id, str):
        for key, value in nodes.items():
            if isinstance(key, str) and key.lower() == node_id.lower():
                node_meta = value
                break

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
    }


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
    return None


def _extract_sensor_values(telemetry):
    if not isinstance(telemetry, dict):
        return {}
    device = telemetry.get("deviceMetrics") or telemetry.get("device_metrics") or {}
    env = telemetry.get("environmentMetrics") or telemetry.get("environment_metrics") or {}
    return {
        "battery_level": device.get("batteryLevel") or device.get("battery_level"),
        "battery_voltage": device.get("voltage") or device.get("batteryVoltage") or device.get("battery_voltage"),
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

    from_id = packet.get("fromId") or packet.get("from")
    to_id = packet.get("toId") or packet.get("to")
    if from_id is not None:
        from_id = str(from_id)
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
            decoded_json=_json_dumps(decoded),
            raw_json=_json_dumps(packet),
        )

    updates = {"last_seen": rx_time}
    updates.update({k: v for k, v in nodeinfo.items() if v is not None})
    updates.update(
        {k: v for k, v in _extract_nodeinfo_from_interface(interface, from_id).items() if v is not None}
    )
    if hops is not None:
        updates["last_hops"] = hops

    if portnum and any(keyword in str(portnum).upper() for keyword in PING_KEYWORDS):
        updates["last_ping"] = rx_time

    if telemetry:
        updates["last_telemetry"] = rx_time
        updates["telemetry_json"] = _json_dumps(telemetry)
        updates.update({k: v for k, v in _extract_sensor_values(telemetry).items() if v is not None})

    if position:
        updates["last_position"] = rx_time
        updates["position_json"] = _json_dumps(position)

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


def _get_available_channels():
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
    return [dict(row) for row in rows]


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


def _render_messages(channel_key, channels):
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT m.*, n.short_name, n.long_name
            FROM messages m
            LEFT JOIN nodes n ON n.node_id = m.from_id
            WHERE m.channel_key = ?
            ORDER BY m.rx_time DESC
            LIMIT 500
            """,
            (channel_key,),
        ).fetchall()
    messages_list = [dict(row) for row in rows]
    current_channel = _find_channel_info(channels, channel_key)
    return render_template(
        "messages.html",
        messages=messages_list,
        channels=channels,
        current_channel=current_channel,
        auto_refresh_seconds=AUTO_REFRESH_SECONDS,
    )


@app.route("/api/messages/<channel_key>")
def messages_api(channel_key):
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                m.rx_time,
                m.channel_index,
                m.channel_key,
                m.from_id,
                m.to_id,
                m.hops,
                m.portnum,
                m.text,
                m.rx_rssi,
                m.rx_snr,
                n.short_name,
                n.long_name
            FROM messages m
            LEFT JOIN nodes n ON n.node_id = m.from_id
            WHERE m.channel_key = ?
            ORDER BY m.rx_time DESC
            LIMIT 500
            """,
            (channel_key,),
        ).fetchall()
    messages_list = [dict(row) for row in rows]
    return jsonify({"messages": messages_list})


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
                position_count_daily
            FROM nodes
            ORDER BY last_seen DESC
            """
        ).fetchall()
    node_list = [dict(row) for row in rows]
    return jsonify({"nodes": node_list})


app.jinja_env.filters["datetime"] = _format_time
app.jinja_env.filters["value"] = _format_value
app.jinja_env.filters["value_unit"] = _format_value_unit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    start_listener()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
