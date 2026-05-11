# Meshapp

Stores Meshtastic packets in SQLite and serves a simple web UI.

## Run

```bash
uv run python main.py
```

## Environment variables

- `MESH_DEVICE` (default `/dev/ttyACM0`)
- `MESH_CHANNEL` (default `0`)
- `PORT` (default `5000`)
