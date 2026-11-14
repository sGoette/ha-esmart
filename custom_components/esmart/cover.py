"""eSmart blinds and awnings."""

from __future__ import annotations

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
    RAW_MAX,
    TYPE_BLIND,
    cover_position_to_raw,
    raw_to_cover_position,
    raw_to_tilt,
    tilt_to_raw,
)
from .entity import ESmartNodeEntity, nodes_of_type


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

    @property
    def current_cover_tilt_position(self) -> int | None:
        return raw_to_tilt(self.data.get("orientation")) if self._has_tilt else None

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        return None if pos is None else pos == 0

    async def _set(self, position_raw: int, orientation_raw: int | None = None) -> None:
        fields: dict[str, Any] = {"position": position_raw}
        if self._has_tilt:
            fields["orientation"] = orientation_raw if orientation_raw is not None else int(self.data.get("orientation", 0))
        await self._command(**fields)

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._set(0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._set(RAW_MAX)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._set(cover_position_to_raw(kwargs[ATTR_POSITION]))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        await self._set(int(self.data.get("position", 0)), tilt_to_raw(kwargs[ATTR_TILT_POSITION]))
