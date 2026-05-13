import json
import time
from meshtastic.serial_interface import SerialInterface

DEVICE = "/dev/ttyACM0"

def safe(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, dict):
        return {k: safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [safe(x) for x in v]
    return v

iface = SerialInterface(devPath=DEVICE)
# Give the interface a moment to populate its node db.
time.sleep(2)

nodes = getattr(iface, "nodes", {}) or {}
print(f"# {len(nodes)} nodes known to the device\n")

for key, node in nodes.items():
    print(f"=== {key} ===")
    print(json.dumps(safe(node), indent=2, default=str))
    print()

# Also dump our own node info — usually the richest entry.
print("=== myInfo ===")
print(json.dumps(safe(getattr(iface, "myInfo", None)), indent=2, default=str))
print("\n=== localNode config ===")
ln = getattr(iface, "localNode", None)
if ln is not None:
    print(json.dumps(safe(getattr(ln, "localConfig", None)), indent=2, default=str))

iface.close()
