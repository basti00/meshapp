import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, redirect, render_template, url_for
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

DB_PATH = Path(__file__).with_name("meshapp.db")
DEVICE_PATH = os.environ.get("MESH_DEVICE", "/dev/ttyACM0")
DEFAULT_CHANNEL = int(os.environ.get("MESH_CHANNEL", "0"))
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


def _db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rx_time INTEGER NOT NULL,
                channel INTEGER,
                from_id TEXT,
                to_id TEXT,
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
                last_telemetry INTEGER,
                last_position INTEGER,
                battery_level REAL,
                battery_voltage REAL,
                temperature REAL,
                humidity REAL,
                pressure REAL,
                telemetry_json TEXT,
                position_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_time ON messages(channel, rx_time DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_last_seen ON nodes(last_seen DESC)")
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


def _extract_portnum(decoded):
    if not isinstance(decoded, dict):
        return None
    return decoded.get("portnum") or decoded.get("portNum") or decoded.get("portnum_name")


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


def handle_packet(packet):
    if not isinstance(packet, dict):
        return
    decoded = packet.get("decoded") or {}
    portnum = _extract_portnum(decoded)

    rx_time = _normalize_timestamp(packet.get("rxTime") or packet.get("rx_time") or time.time())
    channel = packet.get("channel")
    if channel is None:
        channel = packet.get("channelIndex") or packet.get("chan")
    try:
        channel = int(channel) if channel is not None else None
    except (TypeError, ValueError):
        channel = None

    from_id = packet.get("fromId") or packet.get("from")
    to_id = packet.get("toId") or packet.get("to")
    if from_id is not None:
        from_id = str(from_id)
    if to_id is not None:
        to_id = str(to_id)

    message_text = _extract_text(decoded, portnum)

    _insert_message(
        rx_time=rx_time,
        channel=channel,
        from_id=from_id,
        to_id=to_id,
        portnum=str(portnum) if portnum else None,
        text=message_text,
        rx_rssi=packet.get("rxRssi"),
        rx_snr=packet.get("rxSnr"),
        decoded_json=_json_dumps(decoded),
        raw_json=_json_dumps(packet),
    )

    updates = {"last_seen": rx_time}
    updates.update({k: v for k, v in _extract_nodeinfo(decoded).items() if v is not None})

    if portnum and any(keyword in str(portnum).upper() for keyword in PING_KEYWORDS):
        updates["last_ping"] = rx_time

    telemetry = _extract_telemetry(decoded)
    if telemetry:
        updates["last_telemetry"] = rx_time
        updates["telemetry_json"] = _json_dumps(telemetry)
        updates.update({k: v for k, v in _extract_sensor_values(telemetry).items() if v is not None})

    position = _extract_position(decoded)
    if position:
        updates["last_position"] = rx_time
        updates["position_json"] = _json_dumps(position)

    _upsert_node(from_id, **updates)


def on_receive(packet, interface):
    try:
        handle_packet(packet)
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
    return redirect(url_for("messages"))


@app.route("/messages")
def messages():
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT m.*, n.short_name, n.long_name
            FROM messages m
            LEFT JOIN nodes n ON n.node_id = m.from_id
            WHERE m.channel = ?
            ORDER BY m.rx_time DESC
            LIMIT 500
            """,
            (DEFAULT_CHANNEL,),
        ).fetchall()
    messages_list = [dict(row) for row in rows]
    return render_template(
        "messages.html",
        messages=messages_list,
        default_channel=DEFAULT_CHANNEL,
    )


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
    return render_template("nodes.html", nodes=node_list)


app.jinja_env.filters["datetime"] = _format_time
app.jinja_env.filters["value"] = _format_value


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    start_listener()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
