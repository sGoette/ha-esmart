# ha-esmart

Home Assistant custom integration for eSmart (myesmart.com) apartments. Unofficial, reverse-engineered client of the eSmart XMPP protocol. Protocol notes: `docs/protocol.md`.

## This repo is PRIVATE

The user does not work for eSmart. Never create a public remote, publish to HACS, or share code/protocol details with external services. Publishing is only the user's decision.

## HARD RULE: only switch eSmart devices the user has explicitly declared

Some smart devices hang off eSmart-controlled on/off sockets. Switching them is not acceptable.

- Never send an eSmart message with `method: "CMD"` for a node the user has not explicitly declared as switchable.
- Declared nodes are the integration's "switchable devices" option (and, for the old spike client, `homebridge-esmart/spike/.esmart-allow.json`). Only the user changes them. Claude never adds, widens or works around them, and never derives "declared" from a name or type.
- Even for declared nodes, only `CMD`/`operation` with fields of that node. Never `scene`, never `tablet_operation` (holiday mode).
- The integration defaults to read-only and enforces the allow-list in the client (single choke point `ESmartClient.command`). Do not add code paths that bypass it.
- Claude never toggles entities mapped to undeclared nodes, in the UI or via the HA API.
- Always allowed: connecting, listening to the `INFO` stream, `method: "GET"` requests, registering/unregistering test XMPP accounts, sending a pairing code.

## Dev setup

- `.venv` with `homeassistant` + `slixmpp`; run `scripts/develop` to start HA with `config/` (custom_components symlinked).
- Test credentials of the paired test device: `homebridge-esmart/spike/.esmart-test.json` (never commit credentials).
