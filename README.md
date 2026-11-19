# ha-esmart

Unofficial Home Assistant integration for eSmart (myesmart.com) apartments. Not affiliated with or endorsed by eSmart. It uses the same reverse-engineered protocol as the eSmart app, which eSmart may change at any time. Use at your own risk.

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
| Building door opener | `lock` per door (opt-in, see below) |

Devices are created automatically from the apartment structure, with rooms as areas.

## Safety: read-only by default

Nothing can be switched until you select devices under **Settings → Devices & services → eSmart → Configure**. Leave out sockets that power other equipment. Scenes and holiday mode are never controlled by this integration.

**Door opener (opt-in):** if you tick **Allow opening doors** in the same dialog, every door your eSmart system knows (e.g. the building entrance) appears as a `lock`. Unlocking releases the door opener, like the door button in the eSmart app; the lock shows "unlocked" for 10 s and then "locked" (ready) again. Locking does nothing, the door closes by itself. Through the HomeKit Bridge it shows up in Apple Home as a lock (Face ID required on the lock screen, Siri "Unlock …").

## Install (e.g. Home Assistant OS in a TrueNAS Scale VM)

### Before you start

- **Home Assistant version**: use a recent release (developed on 2026.11). Older versions work but don't show the eSmart logos, because they can't load brand images from a custom integration.
- **Internet access**: on first start Home Assistant installs the `slixmpp` library from PyPI, and the integration connects out to `xmpp.myesmart.net` on TCP port 5222. No incoming ports are needed.
- **eSmart panel nearby**: pairing codes expire quickly.

### 1. Install via HACS (recommended, enables updates from the UI)

1. Install [HACS](https://hacs.xyz) if you don't have it yet.
2. **HACS → ⋮ → Custom repositories**: repository `https://github.com/sGoette/ha-esmart`, type **Integration** → Add.
3. Search for **eSmart** in HACS → **Download**, then restart Home Assistant.

**Updating later**: when a new version is available, Home Assistant shows it under **Settings → Updates** (and in HACS). Click **Update**, then restart.

<details><summary>Manual install without HACS</summary>

Copy the folder `custom_components/esmart` from this repo to `/config/custom_components/esmart` on the Home Assistant machine (e.g. via the **Samba share** or **Terminal & SSH** add-on), then restart Home Assistant. To update, copy the folder again and restart.
</details>

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

### Releases

The version in `custom_components/esmart/manifest.json` is the source of truth. When a push to `main` raises it,
the GitHub Action `.github/workflows/release.yml` creates the tag and release `v<version>` (notes = commit
messages since the previous release). HACS then offers the update in Home Assistant.

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
