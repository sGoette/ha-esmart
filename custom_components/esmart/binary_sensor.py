"""eSmart apartment-level binary sensors (read-only)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ESmartConfigEntry
from .entity import ESmartEntity, apartment_device


async def async_setup_entry(hass: HomeAssistant, entry: ESmartConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    client = entry.runtime_data
    async_add_entities([ESmartHolidayMode(client), ESmartConnected(client)])


class ESmartHolidayMode(ESmartEntity, BinarySensorEntity):
    _attr_translation_key = "holiday_mode"

    def __init__(self, client) -> None:
        super().__init__(client)
        self._attr_unique_id = f"{client.apartment_id}-holiday_mode"
        self._attr_device_info = apartment_device(client)

    @property
    def is_on(self) -> bool | None:
        hm = self.client.extra.get("holiday_mode")
        if not isinstance(hm, dict):
            return None
        return str(hm.get("onoff")).lower() == "on"

    def _relevant(self, changed) -> bool:
        return changed is None


class ESmartConnected(ESmartEntity, BinarySensorEntity):
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client) -> None:
        super().__init__(client)
        self._attr_unique_id = f"{client.apartment_id}-connected"
        self._attr_device_info = apartment_device(client)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.client.available

    def _relevant(self, changed) -> bool:
        return changed is None
