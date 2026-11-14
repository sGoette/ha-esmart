# ha-esmart

Unofficial Home Assistant integration for eSmart (myesmart.com) apartments. **Private, not for distribution.**

Home Assistant connects as its own paired remote device, like the eSmart app, over XMPP. No browser and no eSmart JavaScript are needed.

## What you get

| eSmart | Home Assistant |
|---|---|
| Lamps / dimmer | `light` (on/off, brightness) |
| Stores / Markise | `cover` (position, slat tilt where available) |
| Heating zones | `climate` (current and target temperature, heating/idle) |
| Per-device power | `sensor` (W, diagnostic) |
| Apartment meters | `sensor` (W, flow, temperature) |
| Holiday mode, connection | `binary_sensor` (read-only) |

Devices are created automatically from the apartment structure, with rooms as areas.

## Safety: read-only by default

Nothing can be switched until you select devices under **Settings → Devices & services → eSmart → Configure**. Leave out sockets that power other equipment. Scenes and holiday mode are never controlled by this integration.

## Install

1. Copy `custom_components/esmart` into your Home Assistant `config/custom_components/`, then restart.
2. **Settings → Devices & services → Add integration → eSmart**:
   - **Pair a new device**: registers a new remote device, then asks for a pairing code from the eSmart panel. Enter it right away, because codes expire quickly.
   - **Use an already paired device**: JID and password of an existing remote device.

Remove a device later by unpairing it on the eSmart panel.

## Development (macOS)

```bash
python3 -m venv .venv && .venv/bin/pip install homeassistant slixmpp pytest
.venv/bin/python -m pytest              # offline tests (allow-list guard, conversions)
.venv/bin/python scripts/smoke_client.py # read-only live test with the spike test device
./scripts/develop                        # HA on http://localhost:8123 with the integration linked in
```

Protocol notes: [docs/protocol.md](docs/protocol.md).
