"""eSmart lights (on/off and dimmable)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ESmartConfigEntry
from .convert import TYPE_DIMMER, TYPE_LIGHT, brightness_to_pct, is_on, parse_dimming, pct_to_brightness
from .entity import ESmartNodeEntity, nodes_of_type


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    client = entry.runtime_data
    entities: list[LightEntity] = []
    for room, node in nodes_of_type(client, TYPE_LIGHT):
        entities.append(ESmartLight(client, room, node))
    for room, node in nodes_of_type(client, TYPE_DIMMER):
        entities.append(ESmartDimmer(client, room, node))
    async_add_entities(entities)


class ESmartLight(ESmartNodeEntity, LightEntity):
    _attr_name = None
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool | None:
        return is_on(self.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(onoff="on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(onoff="off")


class ESmartDimmer(ESmartNodeEntity, LightEntity):
    _attr_name = None
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _last_pct = 100

    @property
    def _pct(self) -> int:
        return parse_dimming(self.data.get("dimming"))

    @property
    def is_on(self) -> bool | None:
        if "dimming" not in self.data:
            return None
        return self._pct > 0

    @property
    def brightness(self) -> int | None:
        pct = self._pct
        if pct > 0:
            self._last_pct = pct
        return pct_to_brightness(pct) if pct else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        pct = brightness_to_pct(kwargs[ATTR_BRIGHTNESS]) if ATTR_BRIGHTNESS in kwargs else self._last_pct
        await self._command(dimming=f"{pct}%")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(dimming="0%")
