"""Offline tests: optimistic state after commands (no flip-flop in HomeKit), cover motion, setpoint rounding."""

import asyncio

import pytest

from custom_components.esmart import convert, entity
from custom_components.esmart.climate import ESmartHeating
from custom_components.esmart.cover import ESmartCover
from custom_components.esmart.light import ESmartDimmer, ESmartLight

from .test_client import make_client

ROOM = {"name": "Testraum"}


def run(coro):
    return asyncio.run(coro)


def make(cls, node, data, allowed=True):
    c = make_client(allowed=(node["id"],) if allowed else ())
    c.nodes[node["id"]] = data
    e = cls(c, ROOM, node)
    e.async_write_ha_state = lambda: None  # no hass in offline tests
    return c, e


def stream(c, e, nid, data):
    """Simulate an INFO/data frame for one node reaching the entity."""
    c.nodes[nid] = data
    e._on_update({nid})


def test_field_matches():
    assert convert.field_matches("onoff", "on", "ON")
    assert not convert.field_matches("onoff", "on", "off")
    assert convert.field_matches("dimming", "50%", "51%")
    assert not convert.field_matches("dimming", "50%", "20%")
    assert convert.field_matches("position", 287, 286)
    assert not convert.field_matches("position", 1024, 500)
    assert convert.field_matches("setpoint", 23, 23.0)
    assert not convert.field_matches("setpoint", 23, 24)
    assert not convert.field_matches("onoff", "on", None)


def test_light_ignores_stale_stream_until_confirmed():
    c, e = make(ESmartLight, {"id": 41, "type_id": 2}, {"onoff": "off", "power": 0})
    run(e.async_turn_on())
    assert e.is_on is True  # immediately, before eSmart answers
    stream(c, e, 41, {"onoff": "off", "power": 0})  # stale frame right after the CMD
    assert e.is_on is True
    stream(c, e, 41, {"onoff": "on", "power": 7})  # confirmation
    assert e._pending is None and e.is_on is True
    stream(c, e, 41, {"onoff": "off", "power": 0})  # switched off on the panel later
    assert e.is_on is False


def test_pending_expires_when_esmart_ignores_the_command(monkeypatch):
    c, e = make(ESmartLight, {"id": 41, "type_id": 2}, {"onoff": "off"})
    now = [1000.0]
    monkeypatch.setattr(entity.time, "monotonic", lambda: now[0])
    run(e.async_turn_on())
    assert e.is_on is True
    now[0] += entity.PENDING_SECONDS + 0.1
    stream(c, e, 41, {"onoff": "off"})
    assert e.is_on is False and e._pending is None


def test_refused_command_sets_no_pending_state():
    c, e = make(ESmartLight, {"id": 41, "type_id": 2}, {"onoff": "off"}, allowed=False)
    with pytest.raises(Exception):
        run(e.async_turn_on())
    assert e._pending is None and e.is_on is False


def test_dimmer_optimistic_brightness():
    c, e = make(ESmartDimmer, {"id": 42, "type_id": 3}, {"dimming": "0%"})
    run(e.async_turn_on(brightness=128))
    assert e.is_on is True and e.brightness == convert.pct_to_brightness(50)
    stream(c, e, 42, {"dimming": "0%"})
    assert e.is_on is True


def test_heating_rounds_half_up_and_clamps():
    c, e = make(ESmartHeating, {"id": 25, "type_id": 6}, {"setpoint": 18, "temperature": 21})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields["setpoint"])

    c.command = capture
    for value in (22.5, 23.5, 30, 10, 21.4):
        run(e.async_set_temperature(temperature=value))
    assert sent == [23, 24, 24, 18, 21]
    assert e.target_temperature == 21  # last value shown immediately


def test_cover_reports_motion_until_target_reached():
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 0, "orientation": 0})
    run(e.async_close_cover())
    assert e.is_closing and not e.is_opening
    assert e.current_cover_position == 100  # real position, not faked
    stream(c, e, 65, {"position": 0, "orientation": 0})  # stale
    assert e.is_closing
    stream(c, e, 65, {"position": 600, "orientation": 0})
    assert e.is_closing
    stream(c, e, 65, {"position": 1020, "orientation": 0})  # within tolerance
    assert not e.is_closing and e._target_raw is None


def test_cover_tilt_shown_immediately_without_motion():
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 512, "orientation": 1024})
    run(e.async_set_cover_tilt_position(tilt_position=100))
    assert e.current_cover_tilt_position == 100 and not e.is_opening and not e.is_closing
    stream(c, e, 65, {"position": 512, "orientation": 1024})  # stale
    assert e.current_cover_tilt_position == 100


def test_cover_motion_stops_when_position_stalls(monkeypatch):
    from custom_components.esmart import cover

    node = {"id": 65, "type_id": 4, "has_orientation": False}
    c, e = make(ESmartCover, node, {"position": 0})
    now = [1000.0]
    monkeypatch.setattr(cover.time, "monotonic", lambda: now[0])
    run(e.async_close_cover())
    stream(c, e, 65, {"position": 300})
    assert e.is_closing
    now[0] += cover.STALL_SECONDS + 0.1  # stopped on the panel at 300
    stream(c, e, 65, {"position": 300})
    assert not e.is_closing
