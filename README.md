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

## Install (e.g. Home Assistant OS in a TrueNAS Scale VM)

### Before you start

- **Home Assistant version**: use a recent release (developed on 2026.11). Older versions work but don't show the eSmart logos, because they can't load brand images from a custom integration.
- **Internet access**: on first start Home Assistant installs the `slixmpp` library from PyPI, and the integration connects out to `xmpp.myesmart.net` on TCP port 5222. No incoming ports are needed.
- **eSmart panel nearby**: pairing codes expire quickly.

### 1. Copy the integration

Copy the folder `custom_components/esmart` from this repo to `/config/custom_components/esmart` on the Home Assistant machine, then restart Home Assistant.

On Home Assistant OS, the easiest ways are the **Samba share** add-on (Mac: Finder → Go → Connect to Server → `smb://homeassistant.local/config`; Windows: `\\homeassistant\config`) or the **Terminal & SSH** add-on (`scp -r`). HACS is not used, because this repo is private.

**Updating later**: copy the folder again (replace the old one) and restart.

### 2. Add and pair

**Settings → Devices & services → Add integration → eSmart**:

- **Pair a new device** (recommended): registers a new remote device for this Home Assistant, then asks for a pairing code from the eSmart panel. Enter it right away.
- **Use an already paired device**: JID and password of an existing remote device. Don't reuse the test device from `spike/`; keep that one for the test client.

Devices, rooms (as areas) and sensors appear automatically.

### 3. Declare switchable devices

A new installation is read-only. Select the devices Home Assistant may switch under **Settings → Devices & services → eSmart → Configure**. This list is per installation and is not copied from another one. Leave out sockets that power other equipment.

### 4. Optional: Energy dashboard

eSmart only streams current power (W), not energy totals. For the Energy dashboard, add a helper per power sensor: **Settings → Devices & services → Helpers → Create helper → Integral sensor** (source: the eSmart power sensor, method: left, metric prefix: k, time unit: hours), then add the resulting kWh sensor under **Settings → Dashboards → Energy**.

### Removing

Delete the integration in Home Assistant, then unpair its device on the eSmart panel.

## Development (macOS)

```bash
python3 -m venv .venv && .venv/bin/pip install homeassistant slixmpp pytest
.venv/bin/python -m pytest              # offline tests (allow-list guard, conversions)
.venv/bin/python scripts/smoke_client.py # read-only live test with the spike test device
./scripts/develop                        # HA on http://localhost:8123 with the integration linked in
```

### Protocol test client (`spike/`)

Standalone Node client for checking whether eSmart changed its protocol. Credentials (`.esmart-test.json`),
the allow-list (`.esmart-allow.json`), `captures/` and `logs/` stay local and are gitignored.

```bash
cd spike && npm ci
node esmart-test.mjs listen 30   # log in, GET structure/config, print the live INFO stream
node esmart-test.mjs serve       # local web UI on http://127.0.0.1:8099 (pairing code, status, raw log)
node esmart-test.mjs register    # new test account (then pair it via serve) · unregister removes it
```

Protocol notes: [docs/protocol.md](docs/protocol.md).
