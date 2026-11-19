"""eSmart XMPP client (no Home Assistant dependencies).

Protocol summary (see docs/protocol.md):
- XMPP account on myesmart.net (in-band registered), SASL PLAIN over mandatory STARTTLS.
- After login the apartment controller ("master") invites us into its MUC room.
- All traffic is JSON in groupchat bodies: {"headers": {...}, "body": {...}}.

SAFETY: `_send_room` is the single choke point for everything sent to the controller.
CMD/operation only for node ids in `allowed_nodes` (via `command`); CMD/door only as
{"id": <door from GET/doors>, "action": "pulse"} and only when `allow_doors` is set (via
`open_door`). Scenes and tablet operations are never sent by this client.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import ssl
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import slixmpp

_LOGGER = logging.getLogger(__name__)

HOST = "xmpp.myesmart.net"
PORT = 5222
DOMAIN = "myesmart.net"
PAIRING_ROOM = f"shared@conference.{DOMAIN}"
PROTOCOL_VERSION = "1.2.0"
MUC_NS = "http://jabber.org/protocol/muc"

DOOR_CONFIRM_TIMEOUT = 10  # seconds to wait for INFO/door after CMD/door
DATA_TIMEOUT = 60  # seconds without a data message before we reconnect
INVITE_TIMEOUT = 60  # seconds after login without an invitation before we reconnect


class ESmartError(Exception):
    """Base error."""


class ESmartNotAllowed(ESmartError):
    """Command refused by the allow-list."""


class ESmartAuthError(ESmartError):
    """Login failed."""


class ESmartTimeout(ESmartError):
    """Something did not happen in time."""


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS context. Prefer certifi (shipped with Home Assistant) over the system store."""
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# --------------------------------------------------------------------------- registration


async def register_account(timeout: float = 15, ssl_context: ssl.SSLContext | None = None) -> tuple[str, str]:
    """Create a new XMPP account (XEP-0077) the same way the eSmart web client does."""
    username = f"w-{secrets.token_hex(7)[:13]}-{10 + secrets.randbelow(90)}"
    password = secrets.token_hex(16)
    header = (
        f"<?xml version='1.0'?><stream:stream to='{DOMAIN}' xmlns='jabber:client' "
        "xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>"
    )

    async def _do() -> None:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        buf = ""

        async def until(marker: str) -> str:
            nonlocal buf
            while marker not in buf:
                chunk = await reader.read(4096)
                if not chunk:
                    raise ESmartError(f"connection closed waiting for {marker}")
                buf += chunk.decode(errors="replace")
            idx = buf.index(marker) + len(marker)
            out, buf = buf[:idx], buf[idx:]
            return out

        try:
            writer.write(header.encode())
            await until("</stream:features>")
            writer.write(b"<starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>")
            await until("<proceed")
            buf = ""
            await writer.start_tls(ssl_context or _ssl_context(), server_hostname=DOMAIN)
            writer.write(header.encode())
            await until("</stream:features>")
            writer.write(
                f"<iq type='set' id='reg' to='{DOMAIN}'><query xmlns='jabber:iq:register'>"
                f"<username>{username}</username><password>{password}</password></query></iq>".encode()
            )
            pattern = re.compile(r"<iq[^>]*id=['\"]reg['\"][^>]*?(/>|>[\s\S]*?</iq>)")
            while not (m := pattern.search(buf)):
                chunk = await reader.read(4096)
                if not chunk:
                    raise ESmartError("connection closed during registration")
                buf += chunk.decode(errors="replace")
            if re.search(r"type=['\"]error['\"]", m.group(0)):
                raise ESmartError(f"registration refused: {m.group(0)[:300]}")
        finally:
            with contextlib.suppress(Exception):
                writer.write(b"</stream:stream>")
                writer.close()

    await asyncio.wait_for(_do(), timeout)
    return f"{username}@{DOMAIN}", password


# --------------------------------------------------------------------------- client


class ESmartClient:
    """Keeps a connection to the eSmart controller and mirrors its state."""

    def __init__(
        self,
        jid: str,
        password: str,
        allowed_nodes: Iterable[int] = (),
        allow_doors: bool = False,
        device_name: str = "Home Assistant",
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._ssl = ssl_context
        self.jid = jid
        self._password = password
        self.nick = jid.split("@", 1)[0]
        self.device_name = device_name
        self.allowed_nodes: set[int] = {int(n) for n in allowed_nodes}
        self.allow_doors = bool(allow_doors)

        self.room: str | None = None
        self.structure: dict[str, Any] | None = None
        self.scenes: list[dict[str, Any]] = []
        self.doors: dict[int, str] = {}  # door id -> name, from GET/doors
        self._door_waiters: dict[int, list[asyncio.Future]] = {}
        self.nodes: dict[int, dict[str, Any]] = {}
        # node data exactly as last seen in INFO/data (without INFO/operation merged in);
        # INFO/operation can confirm a value before the data stream has caught up
        self.stream_nodes: dict[int, dict[str, Any]] = {}
        self.meters: list[dict[str, Any]] = []
        self.extra: dict[str, Any] = {}  # holiday_mode, valves_state, ...
        self.last_data: float | None = None

        self._xmpp: slixmpp.ClientXMPP | None = None
        self._runner: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._stopping = False
        self._session_started: float | None = None
        self._listeners: list[Callable[[set[int] | None], None]] = []
        self._online = asyncio.Event()
        self._in_room = asyncio.Event()
        self._structure_evt = asyncio.Event()
        self._data_evt = asyncio.Event()
        self._doors_evt = asyncio.Event()
        self._auth_failed = False

    # ------------------------------------------------------------------ public api

    @property
    def available(self) -> bool:
        return (
            self.room is not None
            and self.last_data is not None
            and time.monotonic() - self.last_data < DATA_TIMEOUT
        )

    @property
    def apartment_id(self) -> str | None:
        if self.structure:
            return self.structure.get("apartment", {}).get("id")
        return None

    def add_listener(self, cb: Callable[[set[int] | None], None]) -> Callable[[], None]:
        """cb(changed_node_ids) on every state change; None means 'everything / availability'."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    async def start(self) -> None:
        self._stopping = False
        self._runner = asyncio.create_task(self._run(), name="esmart-xmpp")
        self._watchdog = asyncio.create_task(self._watch(), name="esmart-watchdog")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._watchdog,):
            if task:
                task.cancel()
        if self._xmpp:
            self._xmpp.disconnect()
        if self._runner:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._runner, 5)

    async def wait_online(self, timeout: float = 20) -> None:
        await self._wait(self._online, timeout, "login")

    async def wait_in_room(self, timeout: float = 30) -> None:
        await self._wait(self._in_room, timeout, "invitation from the controller")

    async def wait_ready(self, timeout: float = 30) -> None:
        """Logged in, in the room, structure and first data received."""
        deadline = time.monotonic() + timeout
        await self.wait_in_room(timeout)
        if self.structure is None:
            await self.request("structure")
        await self._wait(self._structure_evt, max(1, deadline - time.monotonic()), "structure")
        await self._wait(self._data_evt, max(1, deadline - time.monotonic()), "data")

    async def wait_doors(self, timeout: float = 10) -> None:
        """Wait for the door list (INFO/doors)."""
        if not self._doors_evt.is_set():
            with contextlib.suppress(ESmartError):
                self._send_room("GET", "doors", {})
        await self._wait(self._doors_evt, timeout, "door list")

    async def request(self, type_: str, body: dict[str, Any] | None = None) -> None:
        """Send a read-only GET request (structure, scene, config, capabilities, data, history)."""
        self._send_room("GET", type_, body or {})

    async def command(self, node_id: int, **fields: Any) -> None:
        """Send a CMD/operation for one node. Refused unless the node is allow-listed."""
        node_id = int(node_id)
        if node_id not in self.allowed_nodes:
            _LOGGER.warning("refused CMD for node %s (not declared as switchable): %s", node_id, fields)
            raise ESmartNotAllowed(
                f"eSmart node {node_id} is not declared as switchable (integration options)"
            )
        if not fields:
            raise ValueError("no fields")
        self._send_room("CMD", "operation", {"id": node_id, **fields})

    async def open_door(self, door_id: int, timeout: float = DOOR_CONFIRM_TIMEOUT) -> None:
        """Pulse the door opener (like the door button in the app) and wait for INFO/door.

        Refused unless door opening is enabled in the options and the door is in GET/doors.
        """
        door_id = int(door_id)
        if not self.allow_doors:
            _LOGGER.warning("refused CMD/door for door %s (door opening not enabled)", door_id)
            raise ESmartNotAllowed("door opening is not enabled (integration options)")
        if door_id not in self.doors:
            raise ESmartNotAllowed(f"unknown eSmart door {door_id}")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._door_waiters.setdefault(door_id, []).append(fut)
        try:
            self._send_room("CMD", "door", {"id": door_id, "action": "pulse"})
            _LOGGER.info("eSmart door %s (%s) opened", door_id, self.doors[door_id])
            try:
                await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError as err:
                raise ESmartTimeout(f"no confirmation from the controller for door {door_id}") from err
        finally:
            waiters = self._door_waiters.get(door_id, [])
            if fut in waiters:
                waiters.remove(fut)

    async def send_pair_code(self, code: str | int, timeout: float = 30) -> None:
        """Pair this account with the apartment using the code shown on the eSmart panel."""
        await self.wait_online()
        num = int(str(code).strip())
        assert self._xmpp
        self._join(PAIRING_ROOM)
        await asyncio.sleep(0.5)
        payload = {"jid": self.jid, "code": num, "name": self.device_name}
        self._xmpp.send_message(mto=PAIRING_ROOM, mbody=json.dumps(payload), mtype="groupchat")
        await asyncio.sleep(0.5)
        self._xmpp.send_presence(pto=f"{PAIRING_ROOM}/{self.nick}", ptype="unavailable")
        _LOGGER.debug("pairing code sent")
        await self.wait_in_room(timeout)

    # ------------------------------------------------------------------ internals

    async def _wait(self, evt: asyncio.Event, timeout: float, what: str) -> None:
        if self._auth_failed:
            raise ESmartAuthError("authentication failed")
        try:
            await asyncio.wait_for(evt.wait(), timeout)
        except asyncio.TimeoutError as err:
            if self._auth_failed:
                raise ESmartAuthError("authentication failed") from err
            raise ESmartTimeout(f"timeout waiting for {what}") from err

    def _notify(self, changed: set[int] | None) -> None:
        for cb in list(self._listeners):
            try:
                cb(changed)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("listener failed")

    def _send_room(self, method: str, type_: str, body: dict[str, Any]) -> None:
        if method == "CMD":
            if type_ == "operation":
                if int(body.get("id", -1)) not in self.allowed_nodes:
                    raise ESmartNotAllowed(f"eSmart node {body.get('id')} is not declared as switchable")
            elif type_ == "door":
                if not (
                    self.allow_doors
                    and set(body) == {"id", "action"}
                    and body["action"] == "pulse"
                    and isinstance(body["id"], int)
                    and body["id"] in self.doors
                ):
                    raise ESmartNotAllowed(f"CMD/door {body} is not allowed")
            else:
                raise ESmartNotAllowed(f"CMD/{type_} is never sent by this client")
        if method not in ("GET", "CMD"):
            raise ESmartNotAllowed(f"method {method} is not supported")
        if not self._xmpp or not self.room:
            raise ESmartError("not connected to the eSmart controller")
        payload = {
            "headers": {
                "version": PROTOCOL_VERSION,
                "from": self.jid,
                "to": "master",
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "method": method,
                "type": type_,
                "size": len(json.dumps(body)),
            },
            "body": body,
        }
        _LOGGER.debug("-> %s/%s %s", method, type_, body)
        self._xmpp.send_message(mto=self.room, mbody=json.dumps(payload), mtype="groupchat")

    def _join(self, room: str) -> None:
        assert self._xmpp
        pres = self._xmpp.make_presence(pto=f"{room}/{self.nick}")
        x = ET.Element(f"{{{MUC_NS}}}x")
        ET.SubElement(x, f"{{{MUC_NS}}}history", maxstanzas="0", seconds="0")
        pres.append(x)
        pres.send()

    def _make_xmpp(self) -> slixmpp.ClientXMPP:
        resource = f"ha{secrets.randbelow(1_000_000)}"
        if self._ssl is None:
            self._ssl = _ssl_context()
        # Pass the context in; otherwise slixmpp builds (and blocks the loop on) its own default one.
        xmpp = slixmpp.ClientXMPP(f"{self.jid}/{resource}", self._password, sasl_mech="PLAIN", ssl_context=self._ssl)
        xmpp.enable_direct_tls = False  # server only speaks STARTTLS on 5222
        xmpp.enable_starttls = True
        # The controller subscribes to us when pairing; accept but don't subscribe back.
        xmpp.auto_authorize = True
        xmpp.auto_subscribe = False
        xmpp.register_plugin("xep_0199", {"keepalive": True, "interval": 60, "timeout": 20})
        xmpp.add_event_handler("session_start", self._on_session_start)
        xmpp.add_event_handler("message", self._on_message)
        xmpp.add_event_handler("failed_auth", self._on_failed_auth)
        return xmpp

    async def _run(self) -> None:
        backoff = 5
        while not self._stopping:
            self._xmpp = xmpp = self._make_xmpp()
            done = asyncio.Event()
            xmpp.add_event_handler("disconnected", lambda *_: done.set())
            xmpp.add_event_handler("connection_failed", lambda *_: done.set())
            _LOGGER.debug("connecting to %s:%s", HOST, PORT)
            try:
                xmpp.connect(HOST, PORT)
                await done.wait()
            except asyncio.CancelledError:
                xmpp.disconnect()
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("xmpp connection error")
            finally:
                with contextlib.suppress(Exception):
                    xmpp.cancel_connection_attempt()
                    xmpp.abort()
            self._mark_offline()
            if self._stopping or self._auth_failed:
                break
            if self.last_data and time.monotonic() - self.last_data < 600:
                backoff = 5
            _LOGGER.info("eSmart connection lost, reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    def _mark_offline(self) -> None:
        self._online.clear()
        self._in_room.clear()
        self.room = None
        self._session_started = None
        self._notify(None)

    async def _watch(self) -> None:
        while not self._stopping:
            await asyncio.sleep(10)
            xmpp = self._xmpp
            if not xmpp or not self._session_started:
                continue
            now = time.monotonic()
            if self.room is None and now - self._session_started > INVITE_TIMEOUT:
                _LOGGER.warning("no invitation from the eSmart controller, reconnecting")
                xmpp.disconnect()
            elif self.room and now - max(self.last_data or 0, self._session_started) > DATA_TIMEOUT:
                _LOGGER.warning("no data from the eSmart controller for %ss, reconnecting", DATA_TIMEOUT)
                xmpp.disconnect()
            elif self.room and self.last_data and now - self.last_data > 15:
                self._notify(None)  # availability may have changed

    async def _on_session_start(self, _event: Any) -> None:
        assert self._xmpp
        self._session_started = time.monotonic()
        self._xmpp.send_presence()
        try:
            await self._xmpp.get_roster()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("roster request failed", exc_info=True)
        _LOGGER.debug("online as %s", self._xmpp.boundjid)
        self._online.set()

    def _on_failed_auth(self, _event: Any) -> None:
        _LOGGER.error("eSmart login failed for %s", self.jid)
        self._auth_failed = True
        if self._xmpp:
            self._xmpp.disconnect()

    def _on_message(self, msg: slixmpp.Message) -> None:
        mtype = msg["type"] or "normal"
        body = msg["body"]
        sender = msg["from"]
        if mtype == "normal" and body:
            room = sender.bare
            if not room.endswith(f"@conference.{DOMAIN}") or room == PAIRING_ROOM:
                return
            _LOGGER.debug("invitation into %s", room)
            self.room = room
            self._join(room)
            self._in_room.set()
            if self.structure is None:
                asyncio.get_running_loop().call_later(1, self._request_structure)
            return
        if mtype == "groupchat" and body:
            if sender.bare != self.room or sender.resource == self.nick:
                return
            try:
                data = json.loads(body)
            except ValueError:
                return
            headers = data.get("headers") or {}
            if headers.get("to") not in ("remotes", self.jid):
                return
            self._handle_info(headers, data.get("body"))

    def _request_structure(self) -> None:
        with contextlib.suppress(ESmartError):
            self._send_room("GET", "structure", {})
            self._send_room("GET", "scene", {})
            self._send_room("GET", "doors", {})

    def _handle_info(self, headers: dict[str, Any], body: Any) -> None:
        method, type_ = headers.get("method"), headers.get("type")
        if method != "INFO" or not isinstance(body, dict):
            return
        if type_ == "data" and "nodes" in body:
            was_available = self.available
            self.last_data = time.monotonic()
            changed: set[int] = set()
            for node in body.get("nodes", []):
                nid = int(node["id"])
                self.stream_nodes[nid] = node.get("data") or {}
                if self.nodes.get(nid) != node.get("data"):
                    self.nodes[nid] = node.get("data") or {}
                    changed.add(nid)
            meters = body.get("meters", [])
            meters_changed = meters != self.meters
            self.meters = meters
            extra = {k: v for k, v in body.items() if k not in ("nodes", "meters")}
            extra_changed = extra != self.extra
            self.extra = extra
            self._data_evt.set()
            if not was_available or meters_changed or extra_changed:
                self._notify(None)
            elif changed:
                self._notify(changed)
        elif type_ == "operation" and "operation" in body:
            op = body["operation"]
            nid = int(op.get("id", -1))
            if nid in self.nodes:
                self.nodes[nid] = {**self.nodes[nid], **{k: v for k, v in op.items() if k != "id"}}
                self._notify({nid})
        elif type_ == "structure":
            self.structure = body
            self._structure_evt.set()
            self._notify(None)
        elif type_ == "scene":
            self.scenes = body.get("scenes", [])
        elif type_ == "doors":
            self.doors = {
                int(d["id"]): str(d.get("name") or f"Door {d['id']}")
                for d in body.get("doors", [])
                if isinstance(d, dict) and "id" in d
            }
            self._doors_evt.set()
            self._notify(None)
        elif type_ == "door":
            for fut in self._door_waiters.pop(int(body.get("id", -1)), []):
                if not fut.done():
                    fut.set_result(None)


def iter_nodes(structure: dict[str, Any] | None):
    """Yield (floor, room, node) for every node in a structure message."""
    if not structure:
        return
    for floor in structure.get("apartment", {}).get("floors", []):
        for room in floor.get("rooms", []):
            for node in room.get("nodes", []):
                yield floor, room, node
