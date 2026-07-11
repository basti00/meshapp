# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Meshapp listens to a Meshtastic radio over serial, stores every packet in SQLite (`meshapp.db`), and serves a Flask web UI (messages + nodes views). It runs in production on a Raspberry Pi Zero.

## Commands

* Run the app: `uv run main.py` from the repo root (throws exceptions locally due to the missing serial device, but the web UI and database still work for testing). Env vars: `MESH_DEVICE` (default `/dev/ttyACM0`), `MESH_CHANNEL`, `PORT` (default `5000`), `MESH_AUTO_REFRESH`.
* There are no tests and no linter configured.
* DB backup/restore: `python db_backup.py` (safe while app runs), `python db_restore.py <file_or_dir>` (refuses if `meshapp.db` exists). Backups are tracked with Git LFS.
* Diagnostic scripts (need the serial device, so run on the Pi): `dump_nodes.py`, `dump_messages.py`, `dump_channels.py` print raw Meshtastic data to help understand packet shapes. Sample output is checked in as `dump_nodes.log` / `dump_messages.log`.

## Production Pi

* `ssh user@pizero.tailb42140.ts.net` — permission granted to enter password `4142` via stdin. Repo lives at `~/meshapp` on the Pi (same layout as local).
* A copy of the prod database is at `meshapp.db` locally for reference; it may be out of date.

## Architecture

Everything server-side is in `main.py` (single file, ~1500 lines):

* **Listener thread** (`start_listener`): connects `meshtastic.SerialInterface`, subscribes via pubsub. `handle_packet` is the ingest path: it extracts text/telemetry/position/nodeinfo from each packet and writes to SQLite.
* **Storage pattern — columns + raw JSON**: interesting fields are *promoted to columns* for cheap querying; the verbatim packet is kept in `raw_json` (messages) / `raw_node_json` (nodes, a snapshot of the device's merged node-DB entry from `interface.nodes`). Detail APIs assemble a `sections: {raw, derived}` blob on demand for the UI's expand-for-JSON view. Schema lives in `NODE_COLUMNS` / `CHANNEL_COLUMNS` lists and `init_db()`; `DB_SCHEMA_VERSION` is stamped as `PRAGMA user_version`. There is no migration framework — schema changes have historically been handled with one-off migration code that gets removed after deploy.
* **Threading model for messages**: replies carry `reply_id`; every message gets a `thread_root` at insert time (`_resolve_thread_root`), and out-of-order arrivals are merged by `_reconcile_thread_root`. Un-threadable rows (no packet id) use `COALESCE(thread_root, -id)` at read time. Pagination is *per conversation tree*, not per message: `_load_channel_threads` pages whole trees by recency using a `<recency>:<root>` keyset cursor; live updates (`_load_thread_updates`) return every touched tree in full so the client can replace and re-sort.
* **Channels**: the packet stream only carries a channel index/hash (`channel_key`), never name/PSK — those are snapshotted from `localNode.channels` into the `channels` table on connect (`sync_channels`, wholesale replace). The primary channel arrives with no index and maps to index 0 ("Primary").
* **Frontend**: Jinja templates render the initial page with data embedded as JSON; `static/app.js` handles theme, node/message/channel detail modals (fetching `/api/...` detail endpoints). Scroll-back pagination and the live-update polling loop live inline in `templates/messages.html`. Avatar colors are derived deterministically from the node id (`_node_avatar_colors`).
* **Relay resolution quirk**: only the last byte of the relaying node's id is on the wire, so `message_detail_api` returns all known nodes whose id ends in that byte as `relay_candidates`.

## Conventions

* When exiting, suggest a commit message for the changes you made, 10–20 words max.
