"""Offline tests: allow-list guard, message handling, conversions."""

import asyncio
import json

import pytest

from custom_components.esmart import convert
from custom_components.esmart.client import ESmartClient, ESmartError, ESmartNotAllowed


class FakeXMPP:
    def __init__(self):
        self.sent = []

    def send_message(self, mto, mbody, mtype):
        self.sent.append((mto, json.loads(mbody), mtype))


def make_client(allowed=()):
    c = ESmartClient("w-test-11@myesmart.net", "pw", allowed_nodes=allowed)
    c._xmpp = FakeXMPP()
    c.room = "00-00@conference.myesmart.net"
    return c


def run(coro):
    return asyncio.run(coro)


def test_command_blocked_when_not_allowed():
    c = make_client(allowed=[41])
    with pytest.raises(ESmartNotAllowed):
        run(c.command(33, onoff="on"))
    assert c._xmpp.sent == []


def test_command_blocked_by_default():
    c = make_client()
    with pytest.raises(ESmartNotAllowed):
        run(c.command(41, onoff="on"))
    assert c._xmpp.sent == []


def test_allowed_command_is_sent():
    c = make_client(allowed=[41])
    run(c.command(41, onoff="on"))
    (to, msg, mtype), = c._xmpp.sent
    assert to == c.room and mtype == "groupchat"
    assert msg["headers"]["method"] == "CMD" and msg["headers"]["type"] == "operation"
    assert msg["headers"]["to"] == "master"
    assert msg["body"] == {"id": 41, "onoff": "on"}


def test_scene_and_tablet_operation_never_sent():
    c = make_client(allowed=[41])
    for t in ("scene", "tablet_operation"):
        with pytest.raises(ESmartNotAllowed):
            c._send_room("CMD", t, {"id": 41})
    with pytest.raises(ESmartNotAllowed):
        c._send_room("EXE", "scene", {})
    assert c._xmpp.sent == []


def test_get_allowed_without_allow_list():
    c = make_client()
    run(c.request("structure"))
    assert c._xmpp.sent[0][1]["headers"]["method"] == "GET"


def test_not_connected():
    c = make_client(allowed=[41])
    c.room = None
    with pytest.raises(ESmartError):
        run(c.command(41, onoff="on"))


def test_handle_data_and_operation():
    c = make_client()
    seen = []
    c.add_listener(seen.append)
    c._handle_info({"method": "INFO", "type": "data"}, {"nodes": [{"id": 41, "data": {"onoff": "off", "power": 0}}], "meters": [], "holiday_mode": {"onoff": "off"}})
    assert c.nodes[41]["onoff"] == "off" and c.available
    c._handle_info({"method": "INFO", "type": "operation"}, {"operation": {"onoff": "on", "id": 41}})
    assert c.nodes[41] == {"onoff": "on", "power": 0}
    assert seen[-1] == {41}


def test_conversions():
    assert convert.parse_dimming("42%") == 42
    assert convert.parse_dimming(None) == 0
    assert convert.raw_to_cover_position(0) == 100
    assert convert.raw_to_cover_position(1024) == 0
    assert convert.cover_position_to_raw(100) == 0
    assert convert.cover_position_to_raw(0) == 1024
    assert convert.cover_position_to_raw(convert.raw_to_cover_position(512)) == 512
    # slats: eSmart orientation 0 = open (HA 100), 1024 = closed (HA 0); verified on a real blind
    assert convert.raw_to_tilt(0) == 100
    assert convert.raw_to_tilt(1024) == 0
    assert convert.tilt_to_raw(100) == 0
    assert convert.tilt_to_raw(0) == 1024
    assert convert.tilt_to_raw(convert.raw_to_tilt(512)) == 512
    assert convert.is_on({"deviceOnOff": "on"}) is True
    assert convert.is_on({"onoff": "off"}) is False
    assert convert.is_on({}) is None


# --------------------------------------------------------------------------- doors


def door_client(allow_doors=True):
    c = make_client()
    c.allow_doors = allow_doors
    c._handle_info({"method": "INFO", "type": "doors"}, {"doors": [{"id": 0, "name": "Haupteingang"}]})
    return c


def test_doors_list_parsed():
    assert door_client().doors == {0: "Haupteingang"}


def test_door_blocked_by_default():
    c = door_client(allow_doors=False)
    with pytest.raises(ESmartNotAllowed):
        run(c.open_door(0))
    assert c._xmpp.sent == []


def test_unknown_door_blocked():
    c = door_client()
    with pytest.raises(ESmartNotAllowed):
        run(c.open_door(1))
    assert c._xmpp.sent == []


def test_door_choke_point_only_allows_exact_pulse():
    c = door_client()
    for body in ({"id": 0, "action": "open"}, {"id": 0, "action": "pulse", "x": 1}, {"id": 5, "action": "pulse"}, {"id": "0", "action": "pulse"}):
        with pytest.raises(ESmartNotAllowed):
            c._send_room("CMD", "door", body)
    with pytest.raises(ESmartNotAllowed):
        door_client(allow_doors=False)._send_room("CMD", "door", {"id": 0, "action": "pulse"})
    assert c._xmpp.sent == []


def test_operation_choke_point_checks_allow_list():
    c = make_client(allowed=[41])
    with pytest.raises(ESmartNotAllowed):
        c._send_room("CMD", "operation", {"id": 33, "onoff": "on"})
    assert c._xmpp.sent == []


def test_open_door_sends_pulse_and_waits_for_confirmation():
    c = door_client()

    async def go():
        task = asyncio.create_task(c.open_door(0, timeout=2))
        await asyncio.sleep(0.05)
        c._handle_info({"method": "INFO", "type": "door"}, {"id": 0})
        await task

    run(go())
    (to, msg, _), = c._xmpp.sent
    assert msg["headers"]["method"] == "CMD" and msg["headers"]["type"] == "door"
    assert msg["body"] == {"id": 0, "action": "pulse"}


def test_open_door_without_confirmation_times_out():
    c = door_client()
    with pytest.raises(ESmartError):
        run(c.open_door(0, timeout=0.05))
    assert c._door_waiters.get(0) == []
