# eSmart protocol notes

Reverse-engineered from the eSmart web client (`eSDK.min.js` 2016, Strophe.js) and verified live on 2026-11-14. Private, do not publish.

## Transport

- XMPP server: ejabberd at `xmpp.myesmart.net`, domain `myesmart.net`.
  - `5222`: client port, STARTTLS **required**, Let's Encrypt certificate for `myesmart.net`. Direct TLS is not offered.
  - `5281/http-bind`: BOSH (used by the web client).
- SASL: `PLAIN`, `DIGEST-MD5`, `X-OAUTH2`, `SCRAM-SHA-1` are advertised. **SCRAM-SHA-1 fails** with xmpp.js (`<bad-protocol/>`), so use `PLAIN` (safe under mandatory TLS).
- In-band registration (XEP-0077) is open. The web client creates `w-<13 hex>-<10..99>@myesmart.net` with a random 32-hex password.
- The web client stores `jid%password` AES-encrypted in localStorage `cred` (CryptoJS passphrase hardcoded in eSDK).

## Pairing

1. Log in, send presence.
2. Join MUC `shared@conference.myesmart.net` (nick = JID local part), post groupchat JSON
   `{"jid": "<bare jid>", "code": <int from panel>, "name": "<device name>"}`, leave.
3. The controller `<controller id>@myesmart.net` (format `00-00-xx-xx-xx-xx-xx`) sends `subscribe`. Accept it (`subscribed`). Roster becomes `both`.
4. The controller sends a `type="normal"` message invitation from its room `<controller>@conference.myesmart.net` (resource `/Smack`).

## Session

- After every login, the controller re-sends the invitation within ~1 s (verified). Join the room with nick = JID local part and `<history maxstanzas="0" seconds="0"/>`.
- Ignore groupchat messages from our own nick and messages whose `headers.to` is neither `remotes` nor our bare JID.

## Messages

```json
{"headers": {"version": "1.2.0", "from": "<bare jid>", "to": "master", "timestamp": "...",
             "method": "GET|CMD|INFO|EXE", "type": "...", "size": <len(json(body))>},
 "body": {}}
```

| Direction | method / type | Body |
|---|---|---|
| ← | `INFO/data` | `{"nodes": [{"id", "data"}], "meters": [...], "holiday_mode": {"onoff"}, "valves_state", "modbus_*_on_error"}`, about once per second |
| ← | `INFO/operation` | `{"operation": {"id", ...changed fields}}`, confirmation ~1 s after a CMD |
| → / ← | `GET/structure` | apartment → floors → rooms → nodes (`id`, `name`, `type_id`, `type_name`, `has_orientation`, ...) and meters (unit, type) |
| → / ← | `GET/scene`, `GET/config`, `GET/capabilities` | scenes list, UI config, controller versions |
| → | `GET/data {"data":"summary"}`, `GET/history {"type","period"}` | not yet explored |
| → | `CMD/operation` | `{"id", "onoff": "on"|"off"}` · `{"id", "dimming": "NN%"}` · `{"id", "position": 0..1024, "orientation": 0..1024}` · `{"id", "setpoint": <°C>}` |
| → | `CMD/scene {"id", "version"}` | executes a scene. **Never sent by this integration** |
| → | `CMD/tablet_operation {"type":"holiday_mode", ...}` | holiday mode. **Never sent by this integration** |

## Node types

| type_id | type_name | data fields |
|---|---|---|
| 2 | Lampe | `onoff`, `power` (W) |
| 3 | Lampe Dim. | `dimming` ("NN%"), `power` |
| 4 | Store | `position` (0 = up/open … 1024 = down), `orientation` (0 = slats open … 1024 = closed, verified), `power`. `has_orientation=false` for the awning |
| 6 | Heating | `temperature`, `setpoint` (integer °C, 18..24 = the 7 panel levels, verified), `deviceOnOff` (heat demand: on while temperature < setpoint; older firmware: `onoff`), `power` |

Command behavior (verified):

- Right after a `CMD`, `INFO/operation` confirms the new value within ~50 ms, but `INFO/data` frames can still carry the old value afterwards (lights: rarely; heating setpoint: observed in HomeKit). Clients must not treat those stale frames as a state change. The integration overlays the sent values until `INFO/data` itself reports them (max 5 s, heating 30 s); covers report opening/closing until the target is reached.
- Out-of-range values (e.g. `setpoint` 25) are **silently ignored**: no error, the old value stays.

Meters in the data stream report instantaneous `power` (W) or `flow_rate` (unit unknown), plus one `temperature` entry. Cumulative counters (Wh, dL per the structure) are not in the stream.

## Open questions

- The awning's position semantics: verify on the real awning. (Blind position and slat direction verified 2026-11-15.)
- `flow_rate` unit.
- Energy totals via `GET/data summary` or `GET/history`.
