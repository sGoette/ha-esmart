# ha-esmart

Home Assistant custom integration for eSmart (myesmart.com) apartments. Unofficial, reverse-engineered client of the eSmart XMPP protocol. Protocol notes: `docs/protocol.md`.

## This repo is PUBLIC (user's decision, 2026-11-19)

The user does not work for eSmart. The repo is public on GitHub and installable as a HACS custom repository. Publishing anywhere else (HACS default list, forums, external services) is only the user's decision.

- Never commit personal or apartment data: credentials, JIDs, apartment/controller ids, room/node/door names, addresses, pairing codes, IPs, logs, captures, `config/` (HA storage). Use placeholders in docs and generic names in tests.
- Before every push, check the diff (and new files) for such data.
- Bump `version` in `manifest.json` for each user-facing change; HACS uses it.

## HARD RULE: only switch eSmart devices the user has explicitly declared

Some smart devices hang off eSmart-controlled on/off sockets. Switching them is not acceptable.

- Never send an eSmart message with `method: "CMD"` for a node the user has not explicitly declared as switchable.
- Declared nodes are the integration's "switchable devices" option (and, for the spike test client, `spike/.esmart-allow.json`). Only the user changes them. Claude never adds, widens or works around them, and never derives "declared" from a name or type.
- Even for declared nodes, only `CMD`/`operation` with fields of that node. Never `scene`, never `tablet_operation` (holiday mode).
- The integration defaults to read-only and enforces the allow-list in the client (single choke point `ESmartClient._send_room`, reached via `command` / `open_door`). Do not add code paths that bypass it.
- Doors (user-approved 2026-11-19): `CMD/door` is allowed only as `{"id": <door from GET/doors>, "action": "pulse"}` and only when the user enabled "Allow opening doors" in the options (default off). Claude never enables that option and never opens a door itself unless the user explicitly asks for that specific test.
- Claude never toggles entities mapped to undeclared nodes, in the UI or via the HA API.
- Always allowed: connecting, listening to the `INFO` stream, `method: "GET"` requests, registering/unregistering test XMPP accounts, sending a pairing code.

## Dev setup

- `.venv` with `homeassistant` + `slixmpp`; run `scripts/develop` to start HA with `config/` (custom_components symlinked).
- Test credentials of the paired test device: `spike/.esmart-test.json` (never commit credentials).
