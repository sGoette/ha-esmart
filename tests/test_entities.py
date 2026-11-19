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
    c.stream_nodes[nid] = data
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


def test_heating_rounds_in_direction_of_change_and_clamps():
    c, e = make(ESmartHeating, {"id": 25, "type_id": 6}, {"setpoint": 23, "temperature": 21})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields["setpoint"])

    c.command = capture
    # each step starts from the value shown after the previous one (optimistic overlay)
    steps = [(22.5, 22), (22.5, 23), (23.5, 24), (23.5, 23), (30, 24), (10, 18), (18.5, 19), (19.4, 20), (19.6, 19)]
    for value, expected in steps:
        run(e.async_set_temperature(temperature=value))
        assert sent[-1] == expected, (value, sent)
        assert e.target_temperature == expected  # shown immediately


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


def test_cover_full_open_close_moves_slats_to_extremes():
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 512, "orientation": 419})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields)

    c.command = capture
    run(e.async_set_cover_position(position=25))  # in between: keep current angle
    run(e.async_close_cover())
    run(e.async_open_cover())
    run(e.async_set_cover_position(position=0))
    run(e.async_set_cover_position(position=100))
    run(e.async_set_cover_position(position=50))  # keeps the angle the open just set
    assert sent == [
        {"position": 768, "orientation": 419},
        {"position": 1024, "orientation": 1024},
        {"position": 0, "orientation": 0},
        {"position": 1024, "orientation": 1024},
        {"position": 0, "orientation": 0},
        {"position": 512, "orientation": 0},
    ]


def test_cover_tilt_only_keeps_position_even_at_extremes():
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 1024, "orientation": 1024})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields)

    c.command = capture
    run(e.async_set_cover_tilt_position(tilt_position=59))
    assert sent == [{"position": 1024, "orientation": 420}]


def test_cover_tilt_drag_sends_only_last_value(monkeypatch):
    from custom_components.esmart import cover

    monkeypatch.setattr(cover, "TILT_DEBOUNCE", 0.05)
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 1024, "orientation": 1024})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields)

    c.command = capture

    async def drag():
        tasks = []
        for tilt in (10, 20, 30, 59):  # HomeKit slider steps while dragging
            tasks.append(asyncio.create_task(e.async_set_cover_tilt_position(tilt_position=tilt)))
            await asyncio.sleep(0.01)
            assert e.current_cover_tilt_position == tilt  # shown immediately
        await asyncio.gather(*tasks)

    run(drag())
    assert sent == [{"position": 1024, "orientation": 420}]
    stream(c, e, 65, {"position": 1024, "orientation": 419})  # eSmart's rounding
    assert e.current_cover_tilt_position == 59  # no jump back
    stream(c, e, 65, {"position": 1024, "orientation": 800})  # changed on the panel later
    assert e.current_cover_tilt_position == 22


def test_cover_open_cancels_waiting_tilt(monkeypatch):
    from custom_components.esmart import cover

    monkeypatch.setattr(cover, "TILT_DEBOUNCE", 0.05)
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 1024, "orientation": 1024})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields)

    c.command = capture

    async def go():
        t = asyncio.create_task(e.async_set_cover_tilt_position(tilt_position=50))
        await asyncio.sleep(0.01)
        await e.async_open_cover()
        await t

    run(go())
    assert sent == [{"position": 0, "orientation": 0}]


def test_cover_tilt_refused_immediately_when_not_switchable():
    node = {"id": 65, "type_id": 4, "has_orientation": True}
    c, e = make(ESmartCover, node, {"position": 1024, "orientation": 1024}, allowed=False)
    with pytest.raises(Exception):
        run(e.async_set_cover_tilt_position(tilt_position=50))
    assert e._pending is None and e.current_cover_tilt_position == 0


def test_awning_sends_no_orientation():
    node = {"id": 97, "type_id": 4, "has_orientation": False}
    c, e = make(ESmartCover, node, {"position": 512, "orientation": 0})
    sent = []

    async def capture(node_id, **fields):
        sent.append(fields)

    c.command = capture
    run(e.async_close_cover())
    assert sent == [{"position": 1024}]


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


def test_heating_operation_confirm_then_stale_data_does_not_flip():
    """Seen live: 24 -> 23.5 showed 23, then 24 again, then 23."""
    c, e = make(ESmartHeating, {"id": 25, "type_id": 6}, {"setpoint": 24, "temperature": 23})
    c.stream_nodes[25] = {"setpoint": 24, "temperature": 23}
    c.add_listener(e._on_update)
    run(e.async_set_temperature(temperature=23.5))
    assert e.target_temperature == 23
    c._handle_info({"method": "INFO", "type": "operation"}, {"operation": {"id": 25, "setpoint": 23}})
    assert e.target_temperature == 23 and e._pending  # confirmation alone does not end the overlay
    c._handle_info({"method": "INFO", "type": "data"}, {"nodes": [{"id": 25, "data": {"setpoint": 24, "temperature": 23}}], "meters": []})
    assert e.target_temperature == 23  # stale frame ignored
    c._handle_info({"method": "INFO", "type": "data"}, {"nodes": [{"id": 25, "data": {"setpoint": 23, "temperature": 23}}], "meters": []})
    assert e.target_temperature == 23 and e._pending is None


# --------------------------------------------------------------------------- door lock

from custom_components.esmart import lock as door_lock  # noqa: E402

from .test_client import door_client  # noqa: E402


def make_lock(monkeypatch, allow_doors=True):
    c = door_client(allow_doors=allow_doors)
    e = door_lock.ESmartDoorLock(c, 0)
    e.async_write_ha_state = lambda: None
    e.hass = None
    calls = []
    monkeypatch.setattr(door_lock, "async_call_later", lambda hass, s, cb: calls.append(s) or (lambda: None))
    return c, e, calls


def confirm_soon(c):
    async def _c():
        await asyncio.sleep(0.02)
        c._handle_info({"method": "INFO", "type": "door"}, {"id": 0})
    return asyncio.ensure_future(_c())


def test_door_lock_unlock_pulses_then_relocks(monkeypatch):
    c, e, calls = make_lock(monkeypatch)
    assert e.is_locked

    async def go():
        confirm_soon(c)
        await e.async_unlock()

    run(go())
    assert c._xmpp.sent[-1][1]["body"] == {"id": 0, "action": "pulse"}
    assert not e.is_locked and not e.is_unlocking and calls == [door_lock.UNLOCKED_SECONDS]
    import time as _time
    from types import SimpleNamespace
    later = _time.monotonic() + door_lock.UNLOCKED_SECONDS
    monkeypatch.setattr(door_lock, "time", SimpleNamespace(monotonic=lambda: later))
    assert e.is_locked


def test_door_lock_lock_sends_nothing(monkeypatch):
    c, e, _ = make_lock(monkeypatch)
    e._unlocked_until = float("inf")
    run(e.async_lock())
    assert e.is_locked and c._xmpp.sent == []


def test_door_lock_refused_without_opt_in(monkeypatch):
    c, e, _ = make_lock(monkeypatch, allow_doors=False)
    with pytest.raises(Exception):
        run(e.async_unlock())
    assert e.is_locked and c._xmpp.sent == []


def test_door_lock_stays_locked_without_confirmation(monkeypatch):
    c, e, _ = make_lock(monkeypatch)
    async def failing(door_id):
        raise door_lock.ESmartError("no confirmation")

    monkeypatch.setattr(c, "open_door", failing)
    with pytest.raises(Exception):
        run(e.async_unlock())
    assert e.is_locked and not e.is_unlocking
