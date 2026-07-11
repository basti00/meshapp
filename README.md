# Meshapp

Stores Meshtastic packets in SQLite and serves a simple web UI.

## Run

```bash
uv run main.py
```

## Environment variables

- `MESH_DEVICE` (default `/dev/ttyACM0`)
- `MESH_CHANNEL` (default `0`)
- `PORT` (default `5000`)

## Redeploy on prod via ssh

```bash 
ssh user@pizero.tailb42140.ts.net
```

```bash
 cd ~/meshapp/ && git pull && sudo systemctl restart meshapp.service
```
