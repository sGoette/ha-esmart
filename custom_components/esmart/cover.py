"""eSmart blinds and awnings."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ESmartConfigEntry
from .convert import (
    POSITION_TOLERANCE,
    RAW_MAX,
    TYPE_BLIND,
    cover_position_to_raw,
    field_matches,
    raw_to_cover_position,
    raw_to_tilt,
    tilt_to_raw,
)
from .entity import ESmartNodeEntity, nodes_of_type

MOVE_TIMEOUT = 120  # s, longest plausible travel time
STALL_SECONDS = 8  # position unchanged this long -> stopped (e.g. stopped on the panel)
TILT_DEBOUNCE = 0.7  # s the tilt slider must rest before the angle is sent to eSmart


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    client = entry.runtime_data
    async_add_entities(ESmartCover(client, room, node) for room, node in nodes_of_type(client, TYPE_BLIND))


class ESmartCover(ESmartNodeEntity, CoverEntity):
    _attr_name = None

    def __init__(self, client, room, node) -> None:
        super().__init__(client, room, node)
        self._has_tilt = bool(node.get("has_orientation"))
        self._attr_device_class = CoverDeviceClass.BLIND if self._has_tilt else CoverDeviceClass.SHADE
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
        if self._has_tilt:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features

    @property
    def current_cover_position(self) -> int | None:
        return raw_to_cover_position(self.data.get("position"))

    _tilt_sent: int | None = None  # last orientation we requested
    _tilt_seq = 0  # bumped by every tilt/position request; a debounced tilt only sends if still latest

    @property
    def current_cover_tilt_position(self) -> int | None:
        if not self._has_tilt:
            return None
        raw = self.data.get("orientation")
        # eSmart reports e.g. 419 after being sent 420; keep showing the requested angle so the
        # HomeKit slider does not jump back a step after release
        if self._tilt_sent is not None and field_matches("orientation", self._tilt_sent, raw):
            raw = self._tilt_sent
        return raw_to_tilt(raw)

    _target_raw: int | None = None
    _target_until = 0.0
    _last_raw: int | None = None
    _last_move = 0.0

    def _optimistic(self, fields: dict[str, Any]) -> dict[str, Any]:
        # The position changes gradually while the motor runs. Instead of faking it, report
        # opening/closing until the target is reached, so HomeKit keeps its target slider.
        if "position" in fields:
            self._target_raw = int(fields["position"])
            now = time.monotonic()
            self._target_until = now + MOVE_TIMEOUT
            self._last_move = now
        return {k: v for k, v in fields.items() if k != "position"}

    def _direction(self) -> int:
        """-1 opening (raw decreasing), +1 closing, 0 idle."""
        live = self.client.nodes.get(self.node_id, {}).get("position")
        if self._target_raw is None or live is None:
            return 0
        diff = self._target_raw - int(live)
        if abs(diff) <= POSITION_TOLERANCE:
            return 0
        return 1 if diff > 0 else -1

    def _on_update(self, changed: set[int] | None) -> None:
        if self._target_raw is not None:
            now = time.monotonic()
            live = self.client.nodes.get(self.node_id, {}).get("position")
            if live != self._last_raw:
                self._last_raw, self._last_move = live, now
            if self._direction() == 0 or now >= self._target_until or now - self._last_move >= STALL_SECONDS:
                self._target_raw = None
        super()._on_update(changed)

    @property
    def is_opening(self) -> bool:
        return self._direction() < 0

    @property
    def is_closing(self) -> bool:
        return self._direction() > 0

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        return None if pos is None else pos == 0

    async def _set(self, position_raw: int, orientation_raw: int | None = None) -> None:
        self._tilt_seq += 1  # supersedes a tilt still waiting in its debounce
        fields: dict[str, Any] = {"position": position_raw}
        if self._has_tilt:
            if orientation_raw is None:
                # Like the wall switch: fully up turns the slats horizontal (0), fully down
                # vertical (RAW_MAX). In between, keep the current angle.
                if position_raw in (0, RAW_MAX):
                    orientation_raw = position_raw
                else:
                    orientation_raw = int(self.data.get("orientation", 0))
            fields["orientation"] = orientation_raw
        await self._command(**fields)
        if self._has_tilt:
            self._tilt_sent = orientation_raw

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._set(0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._set(RAW_MAX)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._set(cover_position_to_raw(kwargs[ATTR_POSITION]))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        # HomeKit sends a value for every step while the slider is dragged. Show it right away,
        # but only send the last one, once the slider has rested for TILT_DEBOUNCE seconds.
        orientation_raw = tilt_to_raw(kwargs[ATTR_TILT_POSITION])
        if self.node_id not in self.client.allowed_nodes:
            await self._set(int(self.data.get("position", 0)), orientation_raw)  # raises "not switchable"
            return
        self._tilt_seq += 1
        seq = self._tilt_seq
        self._pending = {**(self._pending or {}), "orientation": orientation_raw}
        self._pending_until = time.monotonic() + self._pending_seconds + TILT_DEBOUNCE
        self.async_write_ha_state()
        await asyncio.sleep(TILT_DEBOUNCE)
        if seq != self._tilt_seq:
            return  # a newer slider value (or open/close) replaced this one
        await self._set(int(self.data.get("position", 0)), orientation_raw)
