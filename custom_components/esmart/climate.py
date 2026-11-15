"""eSmart heating zones."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ESmartConfigEntry
from .convert import TYPE_HEATING, is_on
from .entity import ESmartNodeEntity, nodes_of_type


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    client = entry.runtime_data
    async_add_entities(ESmartHeating(client, room, node) for room, node in nodes_of_type(client, TYPE_HEATING))


class ESmartHeating(ESmartNodeEntity, ClimateEntity):
    _attr_name = None
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    # eSmart panel has 7 levels: 18..24 °C in whole degrees (verified 2026-11-15)
    _attr_target_temperature_step = 1
    _attr_min_temp = 18
    _attr_max_temp = 24

    @property
    def native_current_temperature(self) -> float | None:
        return self.data.get("temperature")

    @property
    def native_target_temperature(self) -> float | None:
        return self.data.get("setpoint")

    @property
    def hvac_action(self) -> HVACAction | None:
        on = is_on(self.data)
        if on is None:
            return None
        return HVACAction.HEATING if on else HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Only HEAT exists; nothing to send."""

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self._command(setpoint=round(min(self.max_temp, max(self.min_temp, float(temp)))))
